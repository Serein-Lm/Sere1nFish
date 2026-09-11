# Word 与 PDF 产物

AI 中枢统一调用 `generate_document_artifact(title, content, output_format)`，`word`/`docx` 与 `pdf` 都接受 Markdown 正文。既有 `generate_word_document` 及其结构化 `sections` 参数保持兼容。

## 分层

- `api.services.artifact_files`：格式 registry 与输入校验。
- `api.services.artifact_markdown`：Word/PDF 共用的 Markdown 排版，支持标题、真实表格、对齐、重复表头、有序/无序嵌套列表、粗体、斜体、删除线、代码、引用及链接。
- `api.services.artifact_word`：生成内存中的 DOCX。
- `api.services.artifact_pdf`：复用 DOCX 排版，通过 `core.office` 转换为 PDF。
- `core.office`：唯一 Office 转 PDF 运行时；来源附件 OCR 也复用此入口。并发最多 2 个，产物转换限时 90 秒，来源转换限时最多 300 秒，每次使用独立临时目录与 LibreOffice profile，超时结束进程组并释放文件。
- `ObjectStorageService` 与 artifacts DAO：私有存储、所有权和元信息。

Markdown 图片保留文字说明，不自动下载外部图片。正文支持的链接为 HTTP、HTTPS 和 mailto。转换失败时不会注册空产物。当前排版不支持合并单元格或复杂的 HTML/CSS 页面复刻。

## 部署与下载

后端镜像需要 LibreOffice、`fonts-noto-cjk`、`fonts-dejavu-core`，以及 Python 的 `python-docx`、`markdown-it-py`；依赖在 `server/Dockerfile.dev` 与 `server/requirements.txt` 中声明。中文样式使用 Noto Sans CJK SC，PDF 内嵌转换时使用的字体。

文件沿用 `/api/v1/artifacts/{artifact_id}/download` 的登录与所有权校验，OSS 签名下载链接按现有服务签发。AI 中枢和钉钉均消费统一产物引用；增加 PDF 不改变下载协议。话术记录中自动挂载附件属于调用侧的后续业务接入，不能仅凭 Skill 已加载推断已生成附件。

最小回归包括 `test_document_artifacts.py`、`test_office_runtime.py` 和 `test_ai_hub_payload.py`；上线后还应实际生成中文 Word/PDF，并通过鉴权下载检查表格、内容与字体。
