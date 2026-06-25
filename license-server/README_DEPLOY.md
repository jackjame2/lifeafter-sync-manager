# 卡密管理系统 - 部署指南

## 项目结构

`
license-server/
├── package.json          # npm 依赖 (wrangler)
├── wrangler.jsonc        # Cloudflare Worker 配置
├── migrations/
│   └── 0001_initial_schema.sql   # D1 数据库表结构
├── src/
│   └── index.js          # Worker 代码 (API + 管理面板)
└── README_DEPLOY.md      # 本文件
`

## 第一步: 安装依赖

`ash
cd license-server
npm install
`

## 第二步: 创建 D1 数据库

`ash
npx wrangler d1 create license-db
`

复制输出的 database_id，粘贴到 wrangler.jsonc 中替换 PLACEHOLDER_DATABASE_ID。

## 第三步: 设置管理员密码

编辑 wrangler.jsonc，将 PLACEHOLDER_CHANGE_ME 替换为你的管理员密码。

或者用 wrangler secret 设置（推荐）:

`ash
npx wrangler secret put ADMIN_PASSWORD
`

如果使用 secret，需要从 wrangler.jsonc 的 ars 块中删除 ADMIN_PASSWORD。

## 第四步: 应用数据库迁移

`ash
npx wrangler d1 migrations apply license-db --remote
`

## 第五步: 部署 Worker

`ash
npx wrangler deploy
`

部署成功后你会得到一个 Worker URL，类似:
https://license-server.YOUR_ACCOUNT.workers.dev

## 第六步: 配置 Python 客户端

编辑 license_client.py，将 _API_BASE 修改为你的 Worker URL:

`python
_API_BASE = "https://license-server.YOUR_ACCOUNT.workers.dev"
`

## 第七步: 生成卡密

1. 打开浏览器访问 https://license-server.YOUR_ACCOUNT.workers.dev/admin
2. 输入管理员密码
3. 在"生成卡密"卡片中设置数量和备注，点击生成
4. 复制或下载生成的卡密，分发给用户

## 管理操作

在管理面板 (Admin Panel) 中你可以:

- **生成卡密**: 批量生成新的激活码
- **查询卡密**: 查看任意卡密的状态
- **禁用卡密**: 远程切断某个用户的使用权限
- **恢复卡密**: 恢复已禁用的卡密
- **解绑卡密**: 解除卡密与设备的绑定，允许换绑到新设备
- **删除卡密**: 永久删除卡密
- **查看日志**: 追踪每个卡密的验证、激活、禁用记录

## 费用

Cloudflare Workers 免费套餐完全够用:
- Workers: 每天 100,000 次请求 (免费)
- D1: 500 MB 存储，每天 500 万次读取 (免费)

对于一个卡密管理系统来说，免费额度绰绰有余。
