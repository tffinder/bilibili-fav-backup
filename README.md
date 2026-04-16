# B 站收藏夹视频备份系统

自动扫描 B 站收藏夹，下载最高清晰度视频，上传到 S3 兼容存储（通过 OpenList），支持多 P 视频、cookie 登录验证、失败重试和网页监控面板。

## 功能特性

✅ **自动备份** - 定时扫描收藏夹，自动下载新视频  
✅ **最高清晰度** - 支持下载 8K、4K、1080P 等最高可用清晰度  
✅ **多 P 支持** - 自动处理分集视频，每 P 独立下载和上传  
✅ **Cookie 登录** - 使用 B 站 cookie 下载高清晰度视频  
✅ **S3 兼容** - 支持 OpenList 等 S3 兼容存储服务  
✅ **智能对比** - 对比已有视频清晰度，有高清则额外下载  
✅ **失败重试** - 下载和上传失败自动重试，带退避策略  
✅ **进度跟踪** - 实时显示下载和上传进度  
✅ **网页监控** - 提供 Web 界面查看状态、日志和配置  
✅ **通知系统** - 错误、警告、完成通知，支持网页和日志  

## 快速开始

### 1. 安装依赖

```bash
# 进入项目目录
cd bilibili-fav-backup

# 安装 Python 依赖
pip install -r requirements.txt

# 安装 biliup（B 站下载工具）
pip install biliup
```

### 2. 配置系统

编辑 `config.yaml` 文件：

```yaml
bilibili:
  cookie: "你的 B 站 Cookie"  # 必需
  fav_id: "你的收藏夹 ID"     # 必需

s3:
  endpoint_url: "https://your-s3-endpoint.com"
  access_key: "your-access-key"
  secret_key: "your-secret-key"
  bucket_name: "your-bucket-name"
```

**获取 B 站 Cookie 方法：**
1. 登录 B 站 (https://www.bilibili.com)
2. 按 F12 打开开发者工具
3. 刷新页面，在 Network 标签找到任意请求
4. 复制 Request Headers 中的 Cookie 值

**获取收藏夹 ID 方法：**
1. 访问你的收藏夹页面
2. URL 格式：`https://space.bilibili.com/你的 UID/favlist?fid=XXX`
3. `fid=` 后面的数字就是收藏夹 ID

### 3. 启动系统

```bash
python main.py
```

启动后访问：http://localhost:8000

### 4. 手动触发同步

- 方法 1：点击网页上的"立即同步"按钮
- 方法 2：调用 API `POST /api/sync/start`

## 目录结构

```
bilibili-fav-backup/
├── core/                      # 核心模块
│   ├── config.py             # 配置管理
│   └── database.py           # 数据库模型
├── services/                  # 服务模块
│   ├── bilibili_api.py       # B 站 API
│   ├── downloader.py         # 视频下载
│   ├── s3_uploader.py        # S3 上传
│   ├── sync_manager.py       # 同步管理
│   ├── scheduler.py          # 定时任务
│   └── notification.py       # 通知服务
├── api/                       # Web API
│   ├── routes.py             # API 路由
│   └── models.py             # 数据模型
├── frontend/                  # 前端界面
│   └── index.html            # 单页应用
├── temp/                      # 临时下载目录
├── logs/                      # 日志文件
├── config.yaml               # 配置文件
├── requirements.txt          # Python 依赖
├── main.py                   # 主入口
└── README.md                 # 说明文档
```

## API 接口

### 状态查询
- `GET /api/status` - 获取系统状态
- `GET /api/stats` - 获取统计信息

### 视频管理
- `GET /api/videos` - 获取视频列表
- `GET /api/videos/{bvid}` - 获取视频详情

### 同步控制
- `POST /api/sync/start` - 启动同步
- `GET /api/sync/progress` - 获取同步进度
- `GET /api/sync/history` - 获取同步历史

### 下载进度
- `GET /api/download-progress` - 获取下载进度

### 通知管理
- `GET /api/notifications` - 获取通知列表
- `POST /api/notifications/mark-read` - 标记为已读

### 配置管理
- `GET /api/config` - 获取配置信息
- `PUT /api/config` - 更新配置

### 测试工具
- `POST /api/test/cookie` - 测试 Cookie
- `POST /api/test/s3` - 测试 S3 连接

### 日志查看
- `GET /api/logs` - 获取系统日志

## 配置说明

### B 站配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `bilibili.cookie` | B 站登录 Cookie | 必填 |
| `bilibili.fav_id` | 收藏夹 ID | 必填 |
| `bilibili.check_cookie_interval` | Cookie 校验间隔 (秒) | 3600 |

### 下载配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `download.quality` | 目标清晰度 (127=8K, 125=4K, 116=1080P) | 127 |
| `download.max_parallel` | 最大并行下载数 | 1 |
| `download.temp_dir` | 临时下载目录 | ./temp |
| `download.retry_times` | 下载失败重试次数 | 3 |
| `download.request_delay` | 请求延迟 (秒，用于风控) | 1.0 |

### S3 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `s3.endpoint_url` | S3 端点 URL | 必填 |
| `s3.access_key` | 访问密钥 | 必填 |
| `s3.secret_key` | 秘密密钥 | 必填 |
| `s3.bucket_name` | 存储桶名称 | 必填 |
| `s3.upload_timeout` | 上传超时时间 (秒) | 300 |
| `s3.retry_times` | 上传失败重试次数 | 3 |
| `s3.rate_limit` | 上传限速 (MB/s) | null |

### 定时任务配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `scheduler.enabled` | 是否启用定时任务 | true |
| `scheduler.cron` | Cron 表达式 (分 时 日 月 星期) | 0 2 * * * |
| `scheduler.timezone` | 时区 | Asia/Shanghai |

### 调试配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `debug.enabled` | 调试模式 | false |
| `debug.log_level` | 日志级别 | INFO |
| `debug.keep_temp_files` | 保留临时文件 | false |

## 工作流程

```
定时器触发 (每天 2:00)
    ↓
校验 B 站 Cookie
    ↓
获取收藏夹视频列表
    ↓
遍历每个视频:
    ├─ 获取分 P 信息
    ├─ 获取可用清晰度
    ├─ 查询 S3 是否存在
    ├─ 对比清晰度
    └─ 决策：跳过 / 下载
    ↓
下载视频 (单线程)
    ├─ 调用 biliup 下载
    ├─ 失败重试 (最多 3 次)
    └─ 成功后上传 S3
    ↓
上传到 S3
    ├─ 规范化文件名
    ├─ 失败重试 (最多 3 次)
    └─ 成功后删除本地文件
    ↓
更新数据库记录
    ↓
发送完成通知
```

## 清晰度代码对照表

| 代码 | 清晰度 | 需要登录 |
|------|--------|----------|
| 127  | 8K     | 是       |
| 126  | Dolby Vision | 是 |
| 125  | 4K     | 是       |
| 120  | 4K     | 是       |
| 116  | 1080P 高码率 | 是 |
| 112  | 1080P+ | 是       |
| 80   | 1080P  | 否       |
| 74   | 720P60 | 是       |
| 64   | 720P   | 否       |
| 48   | 720P   | 是       |
| 32   | 480P   | 否       |
| 16   | 360P   | 否       |

## 常见问题

### Q: Cookie 失效怎么办？
A: Cookie 有效期通常为 30 天。失效后需要在网页配置中更新 Cookie，或通过 API 更新。

### Q: 下载速度慢？
A: 
1. 检查网络连接
2. 确认 Cookie 有效（未登录用户清晰度低）
3. 检查 biliup 是否为最新版本

### Q: 上传失败怎么办？
A:
1. 检查 S3 配置是否正确
2. 使用 `POST /api/test/s3` 测试连接
3. 增加 `s3.upload_timeout` 超时时间
4. 查看日志了解具体错误信息

### Q: 如何修改定时任务时间？
A: 编辑 `config.yaml` 中的 `scheduler.cron` 字段。Cron 表达式格式：`分 时 日 月 星期`

### Q: 可以下载多个收藏夹吗？
A: 当前版本仅支持单个收藏夹。如需多个，请运行多个实例或修改代码。

## 注意事项

1. **存储空间** - 确保本地 temp 目录有足够空间容纳单个最大视频
2. **S3 兼容性** - OpenList 需配置为 S3 兼容接口
3. **Cookie 有效期** - B 站 cookie 通常 30 天有效，需定期更新
4. **带宽限制** - OpenList 可能限速，合理设置超时时间
5. **风控策略** - 单线程下载，避免并发请求被封禁

## 技术栈

- **后端**: Python 3.8+, FastAPI, Uvicorn
- **下载工具**: biliup
- **定时任务**: APScheduler
- **数据库**: SQLite, aiosqlite
- **S3 客户端**: boto3
- **前端**: HTML5, CSS3, JavaScript (原生)
- **日志**: loguru

## 开发计划

- [ ] 支持多个收藏夹
- [ ] 邮件通知
- [ ] 微信/QQ 推送
- [ ] 视频元数据保存（封面、简介等）
- [ ] 批量操作功能
- [ ] Docker 部署支持
- [ ] Vue 3 重构前端

## License

MIT License

## 免责声明

本工具仅供个人学习和备份使用，请勿用于商业用途。下载的视频版权归 B 站 UP 主所有，请在授权范围内使用。
