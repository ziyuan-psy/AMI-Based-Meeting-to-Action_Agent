# 真实 S2 跨会议关联：来源核查清单

仅列本轮实际发布的关联。来源/结构校验通过，关系标签由模型选择，尚未独立人工确认；没有用 gold 打分。
完整工具返回、旧记录哈希与双方证据 witness 见 cross_meeting_links.json。

## 1. TS3006a.sys.s.2 → TS3006b.sys.s.2

旧记录：The User Interface Designer will specify the technical functions.

新记录：The UI Designer will compile a list of functions to be displayed on the LCD screen and design a multi-page LCD menu with layers.

模型选择：continues；变化字段：[]

判断理由：Current item expands the UI function listing to include LCD menu with layers.

实际工具序列：
- get_prior_items {"query": "UI designer LCD menu layers actions", "item_type": "actions"}
- read_prior_item {"item_id": "TS3006a.sys.s.2"}

模型选用原文：
- `TS3006b.B.dialog-act.dharshi.163`：Y you you are going to make a list of what functions are uh are going to to be on the the screen , the menu screen .
- `TS3006b.A.dialog-act.dharshi.305`：The User Interface Designer can design a menu for all these function
- `TS3006a.A.dialog-act.vkaraisk.149`：Uh the User Interface Designer should specify the technical functions .
- `TS3006a.A.dialog-act.vkaraisk.150`：Right ? Yeah ?
- `TS3006a.B.dialog-act.vkaraisk.59`：Yep .
- `TS3006b.B.dialog-act.dharshi.164`：And I'm going to make a a nice menu uh with with <gap> pages and yeah .
- `TS3006b.D.dialog-act.dharshi.367`：With with some l with some layers in it .
- `TS3006b.D.dialog-act.dharshi.368`：So some menus .
- `TS3006b.A.dialog-act.dharshi.327`：Yeah .
- `TS3006b.A.dialog-act.dharshi.328`：Yeah .

人工核对：待确认。

## 2. TS3006a.sys.s.13 → TS3006b.sys.s.4

旧记录：PM(MTD021PM): <vocalsound> Th the least amount of functions possible

新记录：Two groups of functions: simple for public and advanced for experienced users.

模型选择：replaces；变化字段：["scope", "text"]

判断理由：The current item changes the design constraint from minimal functions to a two-tier group approach.

实际工具序列：
- get_prior_items {"query": "two groups of functions simple public advanced", "item_type": "decisions"}
- read_prior_item {"item_id": "TS3006a.sys.s.13"}

模型选用原文：
- `TS3006b.B.dialog-act.dharshi.101`：Yeah that's that's why uh I wanted to to make yeah two uh different uh yeah groups of functions .
- `TS3006b.B.dialog-act.dharshi.102`：The the simple functions for for the the whole public , and the the advanced options for the younger people yeah who are more yeah experienced with uh <disfmarker>
- `TS3006a.A.dialog-act.vkaraisk.248`：<vocalsound> Th the least amount of functions possible

人工核对：待确认。

来源抽查标记：**NEEDS_SEMANTIC_REVIEW**。The model labels a simple/advanced grouping as replacing the prior minimal-function principle. Grouping and minimizing the number of functions may coexist, so the relation may be stronger than the source establishes. Preserve the actual output and independently review whether replacement is warranted. This is separate from the repaired selection metadata contract.

## 3. TS3006b.sys.s.2 → TS3006c.sys.s.2

旧记录：The UI Designer will compile a list of functions to be displayed on the LCD screen and design a multi-page LCD menu with layers.

新记录：ID and ME will create a new page and be creative during this session.

模型选择：continues；变化字段：[]

判断理由：Current item extends the earlier menu/page design initiative; no core scope or deadline change detected.

实际工具序列：
- get_prior_items {"query": "new page", "item_type": "actions"}
- read_prior_item {"item_id": "TS3006b.sys.s.2"}

模型选用原文：
- `TS3006c.A.dialog-act.dharshi.291`：<vocalsound> So I <gap>
- `TS3006c.D.dialog-act.dharshi.309`：Alright so that's uh <disfmarker>
- `TS3006c.A.dialog-act.dharshi.292`：uh make new page and uh be creative .
- `TS3006b.B.dialog-act.dharshi.163`：Y you you are going to make a list of what functions are uh are going to to be on the the screen , the menu screen .
- `TS3006b.B.dialog-act.dharshi.164`：And I'm going to make a a nice menu uh with with <gap> pages and yeah .
- `TS3006b.A.dialog-act.dharshi.327`：Yeah .
- `TS3006b.A.dialog-act.dharshi.328`：Yeah .
- `TS3006b.D.dialog-act.dharshi.367`：With with some l with some layers in it .
- `TS3006b.D.dialog-act.dharshi.368`：So some menus .
- `TS3006a.A.dialog-act.vkaraisk.149`：Uh the User Interface Designer should specify the technical functions .
- `TS3006a.A.dialog-act.vkaraisk.150`：Right ? Yeah ?

人工核对：待确认。

来源抽查标记：**NEEDS_SEMANTIC_REVIEW**。Record wording suggests different actors and potentially different tasks: a UI LCD-menu deliverable versus an ID/ME creative-page activity. Same-topic design work does not by itself establish that the same commitment continues. Review task identity and whether this is an in-meeting procedural activity. The fixed metadata adapter did not choose this relationship.
