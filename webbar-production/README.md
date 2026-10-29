# Web Bar 管理系统（可部署版本）

按已确认需求重构：取消短信验证码、手机号+密码注册登录、取消订桌订金、线下人工确认充值、酒水名称/库存可编辑、管理端桌位状态可更新。前端部署 Netlify，API 与 PostgreSQL 部署 Render。此包是需要填写服务地址和环境变量后部署的项目文件，不代表已替用户创建托管账户或完成线上部署。

## 功能
- 顾客：手机号+密码注册/登录、查看桌位、无订金订桌、查看订桌、查看酒水清单、查看钱包余额与流水。
- 管理员：桌位状态（空闲/使用中/已预订/暂停使用）和容量管理；酒水新增、改名、库存调整、停用；查看顾客和订桌；核实线下到账后手动增加钱包余额；查看操作日志。
- 安全：scrypt 密码哈希、12 小时 JWT、管理员 API 权限校验、输入校验、操作日志、数据库健康检查。

## 部署概览
1. 将整个项目放到 GitHub 仓库。
2. 在 Render 使用 `backend/render.yaml` 创建 Blueprint。配置 `ADMIN_PHONE` 为管理员登录手机号、`ADMIN_PASSWORD` 为随机强密码（至少 12 位）。
3. Render 创建成功后，复制 API 服务的 `https://...onrender.com` 地址。
4. 编辑 `frontend/config.js`，将 `window.WEBBAR_API_BASE` 改成 API 地址；同时把 `backend/render.yaml` 中 `CORS_ORIGINS` 改成你的 Netlify 子域名。
5. 在 Netlify 导入同一 Git 仓库，发布目录设为 `frontend`（仓库根目录已有 netlify.toml）。
6. Netlify 发布完成后，若子域名和配置中的 CORS 地址不同，回 Render 更新 `CORS_ORIGINS`，再重新部署 API。
7. 访问 API 的 `/health`，确认返回 `{"ok":true,"database":"connected"...}`。随后使用管理员手机号+密码登录。

## 重要部署说明
- Render Blueprint 的数据库套餐/可用区域可能随账户、时间变化；创建时以控制台显示为准。不要把生产数据放入临时磁盘或未确认持久性的免费数据库。
- 管理员初始账号通过 `ADMIN_PHONE` / `ADMIN_PASSWORD` 环境变量创建。不要把密码写进代码、GitHub 或 Netlify 前端文件。
- `CORS_ORIGINS` 必须设置为准确的 Netlify 网站 origin（不要带路径和末尾斜线）。
- 取消短信意味着手机号仅被填写并格式校验，系统不会验证号码归属。
- 线下充值接口不验证支付凭证；只有人工核实到账后才能点击确认。每次确认都会增加余额并记日志，请谨慎操作。
- 当前版本没有接入在线支付、自动短信、复杂酒水销售扣库存、密码找回和多店管理。
- 桌位刷新使用约 5 秒轮询，不是 WebSocket；其他设备状态通常会在下一轮刷新后出现。
- 初次上线前请用小额/测试数据验收，不要导入真实顾客资料或钱包余额直到权限、备份和业务流程验证完成。

## 本地测试
在 `backend` 目录：
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
JWT_SECRET='local-test-secret-key-at-least-32-characters' DATABASE_URL='sqlite:///./local.db' uvicorn app.main:app --reload
```
另一个终端运行：
```bash
pip install pytest httpx
JWT_SECRET='test-secret-key-that-is-long-enough-1234567890' pytest -q
```
