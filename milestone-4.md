# Milestone 4: The Ghostwriter (深度代笔)

**目标**：实现“基于大纲的流式长文生成，维持 100 章连贯性”。**这是最复杂的模块。**

### 1. 模块定义

* **输入**：`ChapterNode` (from Milestone 1) + `NovelContext` (Redis)
* **输出**：`FullChapterText` (3000-5000 words)
* **核心逻辑**：Beat Sheet Expansion (分镜扩写) + Sliding Window Memory (滑动窗口记忆)。

### 2. 数据结构 (Data Schema)

```python
# file: schemas/execution.py
class SceneBeat(BaseModel):
    location: str
    characters: List[str]
    action_description: str
    mood: str
    estimated_word_count: int = 800

```

### 3. 开发步骤 (Step-by-Step Instructions)

1. **Step 1: Context Manager (Redis)**
* 创建 `core/memory.py`。
* 实现 `get_context(novel_id)`：聚合“世界观” + “最近 3 章摘要” + “上一段落结尾”。
* 实现 `update_summary(novel_id, chapter_text)`：生成本章摘要并推入 Redis List。


2. **Step 2: Beat Planner**
* 创建 `logic/ghostwriter.py`。
* 实现 `plan_scenes(chapter_summary) -> List[SceneBeat]`。
* *Prompt*：强制要求将 100 字的大纲拆解为 6 个具体场景。


3. **Step 3: Scene Writer Loop**
* 实现 `write_scene(beat, context)`。
* *逻辑*：生成一个场景 -> 保存 -> 更新 Context -> 生成下一个场景。


4. **Step 4: Pipeline Orchestrator**
* 实现 `generate_chapter(chapter_node)`。串联 Planner 和 Writer Loop。



### 4. 验收标准 (Verification)

* **字数检查**：生成的一章正文需 > 3000 字。
* **记忆检查**：连续生成 5 章。检查第 5 章的开头是否自然承接第 4 章的结尾（而不是重新自我介绍）。
* **一致性**：主角的名字在 5 章内保持一致，没有发生突变。
