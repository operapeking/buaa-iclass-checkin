# BUAA iClass Checkin

仅 CLI 模式的北航 iClass 自动签到工具。

本项目整合了 `duaa` 的两阶段 cron 工作流，以及 `BUAASignTool` 的 WebVPN / iClass 访问方式：

1. 每天早上查询当天课表；
2. 为当天每节未签到课程注册一次性 cron 任务；
3. 在每节课上课 **10 分钟后** 自动通过 WebVPN 执行签到。

> 请严格遵循学校相关规章制度，合理使用本工具。不要用于恶意并发请求或破坏系统稳定性的行为。

## 特性

- 纯 CLI，无 GUI 依赖；
- 通过 `d.buaa.edu.cn` WebVPN 访问 iClass；
- 支持校外服务器运行；
- 每天自动获取当天课程；
- 每门课上课后 10 分钟自动签到；
- 签到前会再次检查课程状态，已签则跳过；
- 支持按课程 ID / 排课 ID / 课程名过滤。

## 安装

```bash
git clone git@github.com:operapeking/buaa-iclass-checkin.git
cd buaa-iclass-checkin
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
    "student_id": "你的学号",
    "password": "统一身份认证密码",
    "course_ids": [],
    "sign_delay_minutes": 10
}
```

说明：

- `course_ids`: 空数组表示所有课程；也可以填写课程 ID、排课 ID 或课程名。
- `sign_delay_minutes`: 上课后多少分钟签到，默认 10。

运行安装脚本：

```bash
bash install.sh
```

安装脚本会：

- 安装 Python 依赖：`requests`、`beautifulsoup4`；
- 设置每日 07:00 查询课表的 cron 任务。

## 使用

手动查询当天课表并注册签到任务：

```bash
python3 buasign.py --query
```

查看已注册的签到任务：

```bash
python3 buasign.py --show-cron
```

清除所有本工具创建的签到任务：

```bash
python3 buasign.py --clear-cron
```

手动执行某节课签到：

```bash
python3 buasign.py --checkin <student_id> <schedule_id>
```

## 日志与状态

- 日志文件：`buasign.log`
- 课表缓存：`state/`

这些运行时文件不会被 Git 提交。

## 安全说明

`config.json` 包含统一身份认证密码，已加入 `.gitignore`，不要提交到 Git 仓库。
