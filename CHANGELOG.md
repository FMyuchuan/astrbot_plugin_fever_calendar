# 更新日志

## 0.5.0

- 多用户：每个聊天用户独立登录、独立数据，互相看不到对方的日历
- 跨平台：`/calendar bind` 用短码把多个平台的身份并到同一账号
- 指令：`/calendar login|logout|bind|status|today|tomorrow|week|search|show|add|update|delete|confirm|export|help`
- 能力：重复事件与单次实例、闹钟与倒计时提醒、ICS 导出、5 个 LLM 工具
- 安全：工具与指令共用权限检查；确认码随机生成并绑定到人；凭据与导出文件权限 0600
