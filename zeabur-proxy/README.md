# license-proxy (Zeabur HK 反向代理)

国内可达的"前门":部署在 Zeabur 香港,把所有请求转发到现有的 Cloudflare Worker。
客户端改为连这个代理,即可绕开被 GFW 干扰的 Cloudflare IP。现有 Worker / D1 / 后台都不用动。

```
客户端(国内) → 本代理(Zeabur 香港) → https://license-server.cdjjdfkdjd.workers.dev (Worker)
```

## 在 Zeabur 部署
1. zeabur.com → 用 GitHub 登录 → New Project → 区域选 **Hong Kong**。
2. Deploy Service → Git → 选仓库 `lifeafter-sync-manager`。
3. **Root Directory 设为 `zeabur-proxy`**(只构建这个子目录)。
4. Zeabur 自动识别 Node、`npm start` 启动。拿到一个 `xxx.zeabur.app` 域名。
5. (可选环境变量)`UPSTREAM_HOST` 默认 `license-server.cdjjdfkdjd.workers.dev`,一般不用改。

## 绑自定义域名(可选,推荐)
在 Zeabur 服务的 Domains 里加 `api.icnscsc.top`,按它给的 CNAME 在 Cloudflare DNS 里加一条
**仅 DNS(灰云)** 的记录指过去。客户端最终连 `https://api.icnscsc.top`。
