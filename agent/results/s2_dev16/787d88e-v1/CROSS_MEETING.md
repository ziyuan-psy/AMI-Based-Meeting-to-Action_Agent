# 真实 S2 跨会议关联：来源核查清单

仅列本轮实际发布的关联。来源/结构校验通过，关系标签由模型选择，尚未独立人工确认；没有用 gold 打分。
完整工具返回、旧记录哈希与双方证据 witness 见 cross_meeting_links.json。

## 1. ES2011a.sys.s.7 → ES2011d.sys.s.5

旧记录：Adopted project scope: couches and remote controls.

新记录：We will develop a variety of trendy fruit designs for the remote control.

模型选择：updates；变化字段：["scope"]

判断理由：Current item broadens the project scope to include fruit-design variants for remote controls, thus updating prior scope.

实际工具序列：
- get_prior_items {"query": "fruit designs remote control", "item_type": "decisions"}
- read_prior_item {"item_id": "ES2011a.sys.s.7", "offset": 0, "limit": 5, "window": 0}

模型选用原文：
- `ES2011d.B.dialog-act.dharshi.21`：So we'll come up with a variety of trendy and exciting fruit designs for the remote control .
- `ES2011d.A.dialog-act.dharshi.20`：'S a very bright strawberry . <vocalsound>
- `ES2011d.C.dialog-act.dharshi.1`：It's been a l
- `ES2011a.C.dialog-act.dharshi.66`：Yeah so we <disfmarker> <vocalsound> the project is now couches and remote controls .

人工核对：待确认。

来源抽查标记：**NEEDS_SEMANTIC_REVIEW**。The older source follows a 'Maybe we should design couches' suggestion and vocal reactions. Treating it as an adopted project scope may overstate the discussion. The newer source supports fruit variants for the remote but does not clearly establish that it updates the prior couch/remote scope. Actual source citations pass structural checks, while the old decision and relationship semantics need independent review.

## 2. IS1008a.sys.s.6 → IS1008c.sys.s.6

旧记录：The functional design work is assigned to FIE038 and MIE085.

新记录：Decide on the functional design.

模型选择：continues；变化字段：[]

判断理由：Current item action extends the prior decision that functional design work is assigned to FIE038 and MIE085, and that the next step is to determine the functional design.

实际工具序列：
- get_prior_items {"query": "functional design", "item_type": "decisions"}
- read_prior_item {"item_id": "IS1008a.sys.s.6"}

模型选用原文：
- `IS1008c.A.dialog-act.s9553330.10`：And finally in this meeting we have to decide
- `IS1008c.A.dialog-act.s9553330.11`：and we are to take a decision on the remote control concept and uh the functional design
- `IS1008c.A.dialog-act.s9553330.7`：and uh the presentations from the Christine and uh Agnes and from Mister Ed .
- `IS1008c.D.dialog-act.s9553330.1`：<vocalsound>
- `IS1008c.A.dialog-act.s9553330.13`：So we have forty minutes ,
- `IS1008a.B.dialog-act.s9553330.25`：So , the immediate next step is to start determining the functional design , or
- `IS1008a.A.dialog-act.s9553330.122`：Yes .
- `IS1008a.A.dialog-act.s9553330.124`：To come up with the functional design and uh to discuss with Ed , okay , and how it's going to be work ,
- `IS1008a.A.dialog-act.s9553330.125`：and uh first of all with your user acceptance , okay , how it looks like and how it's going to be work in the market , okay ,
- `IS1008a.B.dialog-act.s9553330.27`：Mm-hmm .
- `IS1008a.A.dialog-act.s9553330.126`：so then we can discuss about uh further things .

人工核对：待确认。

## 3. IS1008c.sys.s.10 → IS1008d.sys.s.4

旧记录：Market the product after the price is determined.

新记录：Coordinate market tests after prototypes and conduct test-marketing.

模型选择：updates；变化字段：["scope"]

判断理由：Expanded scope from 'Market the product after the price is determined' to include coordinating market tests after prototypes and test-marketing.

实际工具序列：
- get_prior_items {"query": "market tests", "item_type": "actions"}
- read_prior_item {"item_id": "IS1008c.sys.s.10"}

模型选用原文：
- `IS1008d.B.dialog-act.s9553330.74`：We probably should do some market tests uh once we have the prototypes and do some orders and things like that and test-market it .
- `IS1008c.D.dialog-act.s9553330.120`：I will market it . <vocalsound>
- `IS1008c.D.dialog-act.s9553330.121`：Once we get a price on it then we can market it .
- `IS1008c.A.dialog-act.s9553330.92`：Yes .

人工核对：待确认。

来源抽查标记：**NEEDS_SEMANTIC_REVIEW**。The model treats adding prototype market tests as a scope update to an earlier marketing task. The quotations support both activities, but they may be distinct tasks in the same marketing plan. They do not explicitly establish that the earlier commitment is being updated. Keep the model output unchanged and independently review relationship identity and changed scope.

## 4. IS1008c.sys.s.7 → IS1008d.sys.s.8

旧记录：Decide to use wood as the material for the remote control case.

新记录：The material chosen for the remote is wood.

模型选择：continues；变化字段：[]

判断理由：Current material selection repeats and extends the earlier wood decision.

实际工具序列：
- get_prior_items {"query": "material remote wood", "item_type": "decisions"}
- read_prior_item {"item_id": "IS1008c.sys.s.7"}

模型选用原文：
- `IS1008d.C.dialog-act.s9553330.21`：the material that we chose was wood ,
- `IS1008d.C.dialog-act.s9553330.22`：and uh the colour would be customisable ,
- `IS1008d.C.dialog-act.s9553330.23`：'cause you can stain the wood whatever colour .
- `IS1008c.C.dialog-act.s9553330.73`：and my recommendations um for the new remote control w um would be to have um have it be made out of wood .
- `IS1008c.C.dialog-act.s9553330.51`：we're <disfmarker> we're <vocalsound> currently uh proposing ,
- `IS1008c.D.dialog-act.s9553330.12`：<vocalsound>

人工核对：待确认。

## 5. TS3004b.sys.s.1 → TS3004d.sys.s.4

旧记录：The primary user group is forty plus; older users may require larger buttons.

新记录：Unclear number and layout of buttons; need specification.

模型选择：continues；变化字段：[]

判断理由：Current item expands and specifies button count/size within the same design topic previously discussed

实际工具序列：
- get_prior_items {"query": "buttons specification", "item_type": "problems"}
- read_prior_item {"item_id": "TS3004b.sys.s.1"}

模型选用原文：
- `TS3004d.B.dialog-act.dharshi.26`：We also don't know how many buttons are required , or what kind of buttons .
- `TS3004d.B.dialog-act.dharshi.31`：I can imagine you need at least four buttons or something . So <disfmarker>
- `TS3004d.B.dialog-act.dharshi.32`：But it's enough room .
- `TS3004b.A.dialog-act.vkaraisk.70`：The um uh the main uh people of interest of this company are forty plus people .
- `TS3004b.A.dialog-act.vkaraisk.71`：So they're old and not younger people .
- `TS3004b.A.dialog-act.vkaraisk.72`：So we have to look at that as well .
- `TS3004b.A.dialog-act.vkaraisk.73`：'Specially old people , maybe bi bigger buttons or something ,
- `TS3004d.C.dialog-act.dharshi.43`：Yeah .
- `TS3004d.C.dialog-act.dharshi.42`：Well we haven't had time to design that , the slide pad .
- `TS3004d.B.dialog-act.dharshi.28`：You have a lot of room if you can slide it open .
- `TS3004d.B.dialog-act.dharshi.30`：Yeah I know .
- `TS3004d.C.dialog-act.dharshi.44`：Um the position ?

人工核对：待确认。

## 6. TS3004b.sys.s.5 → TS3004d.sys.s.9

旧记录：The group does not need a two-level remote control.

新记录：The second level should slide open.

模型选择：replaces；变化字段：["text", "scope"]

判断理由：Current item states that the second level should slide open, effectively replacing the previous stance of no two-level remote control.

实际工具序列：
- get_prior_items {"query": "second level slide", "item_type": "decisions"}
- read_prior_item {"item_id": "TS3004b.sys.s.5", "offset": 0, "limit": 5, "window": 0}

模型选用原文：
- `TS3004d.A.dialog-act.dharshi.59`：No , you gotta slide it .
- `TS3004d.B.dialog-act.dharshi.78`：but I think that's not solid enough .
- `TS3004d.B.dialog-act.dharshi.79`：If that breaks then you're screwed .
- `TS3004b.A.dialog-act.vkaraisk.152`：But do we still need a two level remote control ?
- `TS3004b.A.dialog-act.vkaraisk.153`：Because if we only have that l only f
- `TS3004b.D.dialog-act.vkaraisk.129`：It's only for television now .
- `TS3004b.B.dialog-act.vkaraisk.136`：So I I don't think so .
- `TS3004b.A.dialog-act.vkaraisk.154`：No .

人工核对：待确认。

## 7. TS3004a.sys.s.7 → TS3004d.sys.s.11

旧记录：Use the rechargeable unit as a separate option and sell it separately.

新记录：Include a recharger in the unit.

模型选择：updates；变化字段：["text", "scope"]

判断理由：Current item broadens/shifts from a standalone rechargeable option to in-unit recharging, altering the feature's scope.

实际工具序列：
- get_prior_items {"query": "recharger in unit", "item_type": "decisions"}
- read_prior_item {"item_id": "TS3004a.sys.s.7"}

模型选用原文：
- `TS3004d.A.dialog-act.dharshi.250`：So you can put a recharger in it as well .
- `TS3004a.B.dialog-act.vkaraisk.105`：Maybe uh use it as a separate option . Sell it uh separately .

人工核对：待确认。
