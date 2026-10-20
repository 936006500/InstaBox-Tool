# InstaBox Tool

**版本：0.0.1 · 作者：Pre**

在 InstaBox 上一键安装 **sing-box + Cloudflare Quick Tunnel**，自动配置本机保活。安装后输入 `ibt`，通过中文菜单管理节点、云端机器和系统设置。

## 开始使用

### 1. 准备 API Key

登录 [InstaBox Dashboard](https://dashboard.instabox.sh/)，进入 **API Keys → Create Key**：

- 选择 **Restricted** 时授予 `machines:write`，已包含读取机器权限；Full access Key 也可使用。
- 复制只显示一次的 `ibk_...` 密钥，留到安装时粘贴。
- 若控制台要求先完成账号开通，按页面提示处理，并确认账号能访问要部署的机器。[官方说明](https://instabox.sh/docs/keys-sdk)

### 2. 在目标机器安装

通过 SSH 登录要部署的 InstaBox 机器，以**普通用户**执行，不要加 `sudo`。需要 Linux x86_64、Python 3 和可用的用户级 systemd。

```bash
export PATH="$HOME/.local/bin:$PATH" && bash <(curl -fsSL https://raw.githubusercontent.com/Pretic/InstaBox-Tool/main/setup.sh)
```

按提示粘贴 API Key 并回车，**输入时不显示字符**。验证通过后，安装器自动完成：

1. 安装节点和临时隧道，生成 **IBT-Quick** 链接。
2. 识别当前机器并开启后台 API 保活；无法确定本机时，按列表选择当前这一台。
3. 检查结果并打开管理菜单。未完成的步骤会显示处理提示。

复制完整的 `vless://` 链接导入客户端，即可进行连接测试。

### 3. 打开管理菜单

```bash
ibt
```

输入编号后回车选择功能，`0` 返回或退出。退出菜单后，已启用的节点和保活任务继续运行。

| 菜单 | 可以做什么 |
| --- | --- |
| 1 本机信息 | 查看系统、资源、网络和显示时区 |
| 2 查看节点 | 复制当前节点链接 |
| 3 节点管理 | 修复节点、管理隧道、查看日志、CF 优选 |
| 4 机器保活 | 一键保活、管理目标与自动任务、SSH 凭据换新 |
| 5 机器管理 | 查看全部机器，启动、暂停、停止及管理授权 |
| 6 机器优化 | 管理 Swap、BBR + fq，恢复设置 |
| 7 更新卸载 | 更新工具或卸载 |
| 0 退出菜单 | 退出当前菜单 |

## 常用操作

| 需要做什么 | 命令或入口 |
| --- | --- |
| 查看节点链接 | `ibt show` |
| 一键保活运行中的机器 | **4 机器保活 → 1 一键保活机器**，或 `ibt keepalive auto` |
| 查看保活结果和下次执行时间 | `ibt keepalive status` |
| 生成 CF 优选链接 | **3 节点管理 → 8 优选节点地址**，或 `ibt cf` |
| 更换 API Key | `ibt key set` |
| 更新工具 | `ibt update`，完成后重新输入 `ibt` |

首次安装已为本机配置保活。一键保活会加入当前授权可见的运行中机器；想手动选择几台，进入 **机器保活 → 管理保活目标**。添加 SSH/Mosh 命令、固定域名、时区和系统优化的用法见[使用手册](docs/usage.md)。

## 使用提示

- **Quick 隧道重建会换地址。** 用 `ibt show` 获取当前链接，重新导入客户端；连接异常时运行 `ibt diagnose`。
- **API Key 和节点链接请勿公开。** 隐藏输入框中粘贴后按回车即可。
- **保活需要控制端持续运行。** 它通过后台定时任务执行；控制端已暂停时，需要从控制台恢复。机制与状态说明见[保活说明](docs/keepalive.md)。

## 感谢

- **老王**：[eooce/Sing-box](https://github.com/eooce/Sing-box)
- **Joey**：[byJoey/cfy](https://github.com/byJoey/cfy)

## 友链

[Linux DO](https://linux.do)
