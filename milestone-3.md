# Milestone 3: The Mimic (风格变色龙)

**目标**：建立风格向量库，实现“基于样文的风格化重写”。

### 1. 模块定义

* **输入**：`ReferenceText` (Text File) + `RawContent` (String)
* **输出**：`StyledContent` (String)
* **核心逻辑**：RAG (检索增强) + Style Transfer Prompting。

### 2. 技术栈 (Tech Stack)

* **Vector DB**: ChromaDB (本地轻量级) 或 Faiss。
* **Embeddings**: VolcEngine Embeddings 或 OpenAI text-embedding-3-small。

### 3. 开发步骤 (Step-by-Step Instructions)

1. **Step 1: Style Extractor**
* 创建 `logic/mimic.py`。
* 实现 `analyze_style_profile(text) -> StyleProfile` (JSON: 常用词、句长分布、语气)。


2. **Step 2: Vector Store Setup**
* 实现 `ingest_samples(text_file)`。
* 将样文切分为 300 字的 chunks，存入 ChromaDB。


3. **Step 3: RAG Rewriter**
* 实现 `rewrite(raw_text, style_profile)`。
* *流程*：
1. 用 `raw_text` 去 ChromaDB 检索 3 个最相似的样文片段。
2. 构造 Prompt：`"参考以下样文的文风 [Example 1, 2, 3]，重写这段话：[Raw Text]"`。





### 4. 验收标准 (Verification)

* 输入一段平淡的“他拔出剑冲了上去”。
* 加载“古龙风”样文，输出应类似：“剑光一闪。没有人看清他是如何拔剑的，只有喉咙上的血线...”
