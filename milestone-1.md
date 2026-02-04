# Milestone 1: The Architect (灵感架构师)

**目标**：实现“从一句话灵感生成 100 章结构化大纲”的核心链路。

### 1. 模块定义

* **输入**：`UserIdea` (String, e.g., "赛博朋克世界的修仙者")
* **输出**：`NovelStructure` (JSON, 包含世界观、角色、分卷、100章列表)
* **核心逻辑**：链式推理 (Chain of Thought)。`Idea -> World -> Volume -> Chapter`。

### 2. 数据结构 (Data Schema)

```python
# file: schemas/structure.py
class WorldSetting(BaseModel):
    power_system: str
    world_rules: str
    main_conflict: str

class ChapterNode(BaseModel):
    chapter_num: int
    title: str
    summary: str # 50-100字梗概

class VolumeNode(BaseModel):
    volume_num: int
    title: str
    core_objective: str # 本卷目标
    chapters: List[ChapterNode]

class NovelOutline(BaseModel):
    title: str
    setting: WorldSetting
    volumes: List[VolumeNode]

```

### 3. 开发步骤 (Step-by-Step Instructions)

1. **Step 1: World Builder**
* 创建 `logic/architect.py`。
* 实现 `generate_setting(idea) -> WorldSetting`。
* *Prompt 重点*：要求模型基于灵感扩展 3 个维度的设定（力量、地理、势力）。


2. **Step 2: Volume Planner**
* 实现 `plan_volumes(idea, setting) -> List[VolumeNode]` (仅包含卷名和卷简介，不含章节)。
* *逻辑*：固定生成 4 卷（起、承、转、合）。


3. **Step 3: Chapter Expander**
* 实现 `expand_chapters(volume_summary) -> List[ChapterNode]`。
* *逻辑*：并发或循环调用。每卷生成 25 章。要求第 25 章必须是高潮。


4. **Step 4: Assembler**
* 编写 `main_architect(idea)` 将上述步骤串联，输出完整的 `NovelOutline` JSON。



### 4. 验收标准 (Verification)

* 运行脚本，输入简单 Idea，能在 60 秒内生成包含 100 个章节对象的 JSON 文件。
* 检查第 25、50、75、100 章的 `summary` 是否具备明显的高潮剧情特征。
