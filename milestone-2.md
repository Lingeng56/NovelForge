# Milestone 2: The Editor (智能编辑)

**目标**：实现“对现有大纲的逻辑诊断与自动化修复”。

### 1. 模块定义

* **输入**：`NovelOutline` (JSON) + `EditInstruction` (String, e.g., "增加反派智商")
* **输出**：`NovelOutline` (Refined Version)
* **核心逻辑**：Critic-Refiner Loop (批评-修正循环)。

### 2. 开发步骤 (Step-by-Step Instructions)

1. **Step 1: Logic Analyzer (The Critic)**
* 创建 `logic/editor.py`。
* 实现 `analyze_logic(outline) -> List[Issue]`。
* *Prompt 重点*：让 LLM 扮演“毒舌编辑”，寻找前后矛盾（如：道具遗忘、死人复活）和节奏拖沓点。


2. **Step 2: Pacing Adjuster**
* 实现 `apply_pacing_curve(outline)`。
* *算法*：读取所有章节摘要，计算“紧张度”分数。如果发现连续 10 章都是低紧张度，标记为“注水区域”。


3. **Step 3: Refiner (The Fixer)**
* 实现 `rewrite_volume(volume_data, issues)`。
* *逻辑*：仅重写被标记为有问题的章节摘要，保持其他章节不变，以降低 Token 消耗。



### 3. 验收标准 (Verification)

* 构造一个含有逻辑错误的大纲（例如：第 5 章主角断臂，第 10 章双手持剑）。
* 运行 Editor，检查输出的大纲是否修复了该漏洞（例如：改为单手持剑或义肢）。
