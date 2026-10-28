---
title: 简易流式网页聊天
description: 通过本地 app-server 协议，为 Open Interpreter 构建一个小型网页聊天界面。
---

如果需要完整的桌面、浏览器或无界面体验，请使用 [Interpreter Workstation](https://github.com/openinterpreter/interpreter-workstation)。如果只想学习如何连接原始 OIX，请参考[英文指南中的双文件可运行示例](https://www.openinterpreter.com/docs/terminal/streaming-web-chat)：浏览器只与绑定在 `127.0.0.1` 的 Node 桥接程序通信，桥接程序通过 stdio 启动 `interpreter app-server`。模型凭据始终留在服务器端。

协议顺序是：发送 `initialize` 并等待对应响应；发送 `initialized`；使用 `thread/start` 建立会话；使用 `turn/start` 提交文本。`item/agentMessage/delta` 提供流式文本，`turn/completed` 给出最终状态，`error` 提示错误。桥接程序按请求 ID、线程 ID 和轮次 ID 匹配消息。

示例一次只接受一位本地用户的一轮请求；其他本地标签页共享同一会话，**这不是身份隔离**。示例拒绝批准请求，并在浏览器断开后中断当前轮次。网页只通过 `textContent` 显示文本，不能把 app-server 的 WebSocket 或 OAuth 凭据直接公开给浏览器。远程和多用户部署需要额外的身份认证、权限管理、资源限制及交互式批准界面。参阅[应用服务器](/docs/app-server)和[沙箱与批准](/docs/sandbox)。
