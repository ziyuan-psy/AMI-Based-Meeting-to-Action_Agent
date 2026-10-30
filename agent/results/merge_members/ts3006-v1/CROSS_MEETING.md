# 真实 S2 跨会议关联：来源核查清单

仅列本轮实际发布的关联。来源/结构校验通过，关系标签由模型选择，尚未独立人工确认；没有用 gold 打分。
完整工具返回、旧记录哈希与双方证据 witness 见 cross_meeting_links.json。

## 1. TS3006a.sys.s.4 → TS3006c.sys.s.6

旧记录：Selling price will be 25 Euros.

新记录：The target selling price is 25 Euro per piece.

模型选择：continues；变化字段：[]

判断理由：Current dialogue confirms the same selling price as the prior decision.

实际工具序列：
- get_prior_items {"query": "selling price", "item_type": "decisions"}
- read_prior_item {"item_id": "TS3006a.sys.s.4"}

模型选用原文：
- `TS3006c.C.dialog-act.dharshi.104`：So 'cause we need to sell it for twenty five Euro a piece .
- `TS3006a.A.dialog-act.vkaraisk.130`：Um we can sell them at twenty five Euros . Uh

人工核对：待确认。

## 2. TS3006b.sys.s.4 → TS3006d.sys.s.4

旧记录：The new product will include an LCD screen and speech recognition.

新记录：Make one large LCD screen (LCD screen) with casing.

模型选择：continues；变化字段：[]

判断理由：Current item adds more detail (single large LCD screen with casing) to the LCD-screen feature already adopted in TS3006b.sys.s.4.

实际工具序列：
- get_prior_items {"query": "LCD screen decision", "item_type": "decisions"}
- read_prior_item {"item_id": "TS3006b.sys.s.4"}

模型选用原文：
- `TS3006d.A.dialog-act.dharshi.346`：<vocalsound> So you can just make one big L_C_D_ screen . <vocalsound>
- `TS3006d.D.dialog-act.dharshi.450`：<vocalsound> With some casing around it , yeah .
- `TS3006b.D.dialog-act.dharshi.75`：Um there was some research on new features in a remote control .
- `TS3006b.D.dialog-act.dharshi.76`：Uh about an L_C_D_ screen uh and speech recognition .
- `TS3006b.D.dialog-act.dharshi.77`：Well we got an update for the for the audience . Or the the the targeted group .

人工核对：待确认。
