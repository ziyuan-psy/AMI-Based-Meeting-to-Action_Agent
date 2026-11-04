"""Bounded Windows sharing retries preserve atomicity and respect cancellation."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mta import observation
from mta.control import RunControl,Limits,RunStopped

def sharing_error(winerror=32):
    error=PermissionError(13,'simulated sharing violation')
    error.winerror=winerror
    return error

def test_transient_sharing_failure_retries_only_atomic_replace(tmp_path,monkeypatch):
    target=tmp_path/'state.json';target.write_text('old',encoding='utf-8')
    real=observation.os.replace;calls=[]
    def replace(src,dst):
        calls.append((src,dst))
        if len(calls)<3:raise sharing_error()
        return real(src,dst)
    monkeypatch.setattr(observation.os,'replace',replace)
    monkeypatch.setattr(observation.time,'sleep',lambda _:None)
    observation.write_text(target,'complete new value')
    assert len(calls)==3 and target.read_text(encoding='utf-8')=='complete new value'
    assert len({src for src,_ in calls})==1 and not list(tmp_path.glob('*.tmp'))

@pytest.mark.parametrize('winerror',[None,5])
def test_permanent_denial_is_bounded_and_preserves_previous_target(tmp_path,monkeypatch,winerror):
    target=tmp_path/'state.json';target.write_text('old',encoding='utf-8');calls=[]
    def replace(*args):
        calls.append(args)
        raise sharing_error(winerror)
    monkeypatch.setattr(observation.os,'replace',replace)
    monkeypatch.setattr(observation.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):observation.write_text(target,'new')
    assert len(calls)==(1 if winerror is None else 4)
    assert target.read_text(encoding='utf-8')=='old' and not list(tmp_path.glob('*.tmp'))

def test_cancellation_during_retry_prevents_late_replacement(tmp_path,monkeypatch):
    target=tmp_path/'state.json';target.write_text('old',encoding='utf-8');calls=[]
    control=RunControl(Limits(1,1000000,1,5,1,0,0))
    def replace(*args):calls.append(args);raise sharing_error()
    monkeypatch.setattr(observation.os,'replace',replace)
    monkeypatch.setattr(observation.time,'sleep',lambda _:control.stop('cancelled','CANCELLED'))
    try:
        with pytest.raises(RunStopped):observation.write_text(target,'new',control)
        assert len(calls)==1 and target.read_text(encoding='utf-8')=='old'
    finally:control.seal()
