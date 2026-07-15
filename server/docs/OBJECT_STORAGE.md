# 统一对象存储与迁移

## 设计

业务模块只调用 `api.storage.ObjectStorageService`。统一服务按以下层次处理：

```text
业务服务 -> ObjectStorageService -> Provider Factory -> Local / Aliyun OSS Adapter
                                  -> storage_objects DAO
```

- 新写入由 `object_storage.enabled/provider` 选择 Provider。
- 历史读取按 `storage_objects.provider` 选择 Provider，迁移期间本地和 OSS 对象可以并存。
- 领域集合只保存 `storage_object_id`；`storage_objects` 保存 Object Key、SHA-256、大小、归属和状态。
- Bucket 使用私有读写。普通文件下载先经过本站鉴权，再返回最长 1 小时、默认 5 分钟的签名 URL。
- 手机、采集截图经本站鉴权接口代理读取，避免暴露签名 URL 或依赖 Bucket CORS。
- 阿里云禁止通过默认 OSS 公网域名分发 APK；APK 由本站登录鉴权接口从 OSS 分块读取并流式返回，不落本地文件。
- AK/SK 由 `system_config/object_storage` 加密存储，普通配置 API 只返回脱敏值。

## Bucket 准备

在 OSS 控制台创建 Bucket：

- Bucket：`limo-ai-fish`
- 地域：杭州 `cn-hangzhou`
- 读写权限：私有
- 公共访问：阻止公共访问
- 服务端加密：AES256

运行服务的 RAM 身份至少需要该 Bucket 下的对象上传、读取、元信息读取和删除权限。创建 Bucket 还需要 `oss:PutBucket`，列举账号 Bucket 需要 `oss:ListBuckets`；对象读写凭据不一定拥有这两项管理权限。

配置示例仅使用占位值，真实密钥从管理页面导入：

```json
{
  "enabled": false,
  "provider": "aliyun_oss",
  "bucket": "limo-ai-fish",
  "region": "cn-hangzhou",
  "endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
  "public_endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
  "prefix": "sere1nfish/prod",
  "access_key_id": "<RAM AccessKey ID>",
  "access_key_secret": "<RAM AccessKey Secret>",
  "server_side_encryption": "AES256",
  "presign_ttl": 300,
  "connect_timeout": 5,
  "readwrite_timeout": 60,
  "retry_max_attempts": 3
}
```

## 迁移

先盘点，不连接云端、不写数据库业务引用：

```bash
docker-compose exec -T backend python -m scripts.migrate_object_storage
```

确认 Bucket 和权限后执行迁移：

```bash
docker-compose exec -T backend \
  python -m scripts.migrate_object_storage --apply --concurrency 16
```

迁移过程会：

1. 写入随机探测对象并完成上传、读取、删除健康检查。
2. 并发上传手机截图、采集截图、Word、语音和受保护发布文件。
3. 校验远端大小，并重新下载计算 SHA-256。
4. 单个对象校验成功后更新领域引用。
5. 全部成功后自动设置 `enabled=true` 和 `migration_state=completed`。

迁移是幂等的；相同 `object_id` 和 SHA-256 会复用已上传对象。失败时保留本地文件和失败记录，修复权限或网络后重新执行同一命令即可。

## 验证与清理

```bash
curl -k https://127.0.0.1/health
docker-compose logs --tail=200 backend
```

在“系统配置 -> 运行配置 -> 对象存储”查看当前 Provider、纳管对象数量、容量和最近迁移状态。删除本地源文件前，必须对全部远端对象重新校验大小和 SHA-256 元数据，并确认远端对象数量与 MongoDB 元数据一一对应。

当前运行环境已完成 360 个对象、479625948 字节的迁移和全量校验，本地源文件已经删除，OSS 是唯一文件源。不要再依赖 `legacy_path` 回退，也不要把截图、Word、APK 或采集产物重新写入本地业务目录。

完成迁移后应轮换曾经通过聊天或临时终端传递过的 AK/SK，并只给新 RAM 凭据保留 `limo-ai-fish/sere1nfish/prod/*` 所需的最小对象权限。
