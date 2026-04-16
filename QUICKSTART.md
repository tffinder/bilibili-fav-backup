# 快速使用指南

## 一、安装步骤

### Windows 用户

1. **运行安装脚本**
   ```cmd
   install.bat
   ```
   
   或手动安装：
   ```cmd
   pip install -r requirements.txt
   pip install biliup
   ```

### Linux/Mac 用户

```bash
pip install -r requirements.txt
pip install biliup
```

## 二、获取必要信息

### 1. 获取 B 站 Cookie

1. 打开浏览器，访问 https://www.bilibili.com
2. 登录你的 B 站账号
3. 按 `F12` 打开开发者工具
4. 切换到 **Network** (网络) 标签
5. 刷新页面 (`F5`)
6. 在左侧请求列表中找到任意一个请求（如 `www.bilibili.com`）
7. 点击后在右侧找到 **Request Headers** (请求头)
8. 复制 `Cookie` 的值（一长串字符）

示例：
```
Cookie: buvid3=XXX; b_nut=XXX; ... (很长的一串)
```

### 2. 获取收藏夹 ID

1. 访问你的收藏夹页面
2. URL 格式类似：`https://space.bilibili.com/123456/favlist?fid=789012`
3. `fid=` 后面的数字就是收藏夹 ID

示例：
```
URL: https://space.bilibili.com/123456/favlist?fid=789012
                    ↓                        ↓
                UID: 123456            fav_id: 789012
```

### 3. 配置 S3 信息（OpenList）

如果你使用 OpenList，需要以下信息：
- **Endpoint URL**: S3 接口地址，如 `https://s3.example.com`
- **Access Key**: 访问密钥
- **Secret Key**: 秘密密钥
- **Bucket Name**: 存储桶名称

这些信息由你的 OpenList 服务提供商提供。

## 三、配置系统

1. **编辑配置文件**
   
   复制示例配置文件：
   ```cmd
   copy config.example.yaml config.yaml
   ```
   
   或直接编辑 `config.yaml` 文件。

2. **填写配置**
   
   打开 `config.yaml`，填写以下必填项：
   
   ```yaml
   bilibili:
     cookie: "第一步获取的 Cookie"
     fav_id: "第二步获取的收藏夹 ID"
   
   s3:
     endpoint_url: "你的 S3 端点"
     access_key: "你的访问密钥"
     secret_key: "你的秘密密钥"
     bucket_name: "你的存储桶名称"
   ```

3. **保存文件**

## 四、启动系统

### Windows 用户

双击运行：
```cmd
start.bat
```

或命令行运行：
```cmd
python main.py
```

### Linux/Mac 用户

```bash
python main.py
```

## 五、访问界面

启动成功后，打开浏览器访问：

```
http://localhost:8000
```

你会看到一个漂亮的控制面板，显示：
- 📊 视频统计信息
- 🚀 立即同步按钮
- 📹 最近视频列表
- 🔔 通知中心
- 📝 系统日志

## 六、开始备份

### 方法 1：通过网页

1. 访问 http://localhost:8000
2. 点击 **"🚀 立即同步"** 按钮
3. 确认启动同步
4. 等待同步完成

### 方法 2：通过 API

使用 curl 或其他 HTTP 工具：

```bash
curl -X POST http://localhost:8000/api/sync/start
```

### 方法 3：自动定时任务

系统默认每天凌晨 2 点自动同步一次。

可以在 `config.yaml` 中修改时间：

```yaml
scheduler:
  cron: "0 2 * * *"  # 每天 2:00
  # cron: "30 14 * * *"  # 每天 14:30
```

## 七、查看进度和结果

### 实时进度

网页上会显示：
- 当前正在处理的视频
- 下载进度条
- 上传状态

### 查看日志

- **网页查看**: 访问 http://localhost:8000 滚动到底部"系统日志"区域
- **文件查看**: 打开 `logs/app.log` 文件

### 查看通知

网页右上角"通知中心"会显示：
- ✅ 成功通知
- ⚠️ 警告通知
- ❌ 错误通知

## 八、常见问题排查

### 问题 1: Cookie 无效

**现象**: 提示"Cookie 校验失败"

**解决**:
1. 重新获取 Cookie（Cookie 有效期 30 天）
2. 确保复制了完整的 Cookie 字符串
3. 在网页配置中更新 Cookie

### 问题 2: S3 连接失败

**现象**: 提示"S3 连接失败"或上传超时

**解决**:
1. 检查 S3 配置是否正确
2. 测试连接：`POST http://localhost:8000/api/test/s3`
3. 增加超时时间：修改 `s3.upload_timeout` 为更大的值

### 问题 3: 下载速度慢

**现象**: 下载速度很慢或卡住

**解决**:
1. 检查网络连接
2. 确认 Cookie 有效（未登录用户清晰度低但速度快）
3. 降低目标清晰度：修改 `download.quality` 为 116 (1080P)

### 问题 4: 磁盘空间不足

**现象**: 提示"No space left on device"

**解决**:
1. 清理 `temp` 目录
2. 确保有足够空间（建议至少 10GB）
3. 单个视频可能达到几 GB

### 问题 5: 程序崩溃

**现象**: 程序突然退出

**解决**:
1. 查看 `logs/app.log` 了解错误原因
2. 检查 Python 版本（需要 3.8+）
3. 重新安装依赖：`pip install -r requirements.txt --force-reinstall`

## 九、高级用法

### 修改下载质量

编辑 `config.yaml`:

```yaml
download:
  quality: 125  # 改为 4K
  # quality: 116  # 改为 1080P
  # quality: 80   # 改为 720P
```

### 保留临时文件（调试用）

```yaml
debug:
  keep_temp_files: true  # 保留下载的原始文件
```

### 禁用定时任务

```yaml
scheduler:
  enabled: false  # 不自动同步
```

### 使用代理下载

```yaml
debug:
  biliup_proxy: "http://127.0.0.1:7890"  # 代理地址
```

## 十、API 快速参考

```bash
# 获取系统状态
curl http://localhost:8000/api/status

# 获取视频列表
curl http://localhost:8000/api/videos

# 启动同步
curl -X POST http://localhost:8000/api/sync/start

# 测试 Cookie
curl -X POST http://localhost:8000/api/test/cookie

# 测试 S3 连接
curl -X POST http://localhost:8000/api/test/s3

# 获取通知
curl http://localhost:8000/api/notifications

# 获取日志
curl "http://localhost:8000/api/logs?lines=50"
```

## 十一、停止程序

按 `Ctrl + C` 停止运行中的程序。

Windows 用户使用 `start.bat` 启动的，关闭命令行窗口即可。

---

**祝你使用愉快！** 🎉

如有问题，请查看日志文件或联系技术支持。
