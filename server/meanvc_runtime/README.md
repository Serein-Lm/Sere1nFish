# MeanVC Runtime

该服务是 GPU 媒体平面的私有零样本实时变声适配器。它只监听
`127.0.0.1:8766`，由 Deepfake Gateway 创建短时会话并通过 WebSocket
传输 16 kHz、单声道、16-bit PCM。

- 上游项目：<https://github.com/ASLP-lab/MeanVC>
- 固定版本：`b07024579284975bc8a6a9aa72201d6279b417ab`
- 上游许可证：Apache-2.0
- 输入语义：麦克风原始语音
- 输出语义：保留原始内容并转换为授权参考音色
- 参考音频：默认要求 3 至 15 秒清晰单人语音
- 持久化：参考音频和原始音轨均不落盘，随会话释放

模型文件下载到宿主机挂载的 `/models`，不会写入 Git 或镜像层。API
Token 由只读 secret 文件加载，运行端口不向公网开放。
