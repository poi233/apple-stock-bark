# Apple Stock → Bark

Apple Store **到店取货库存**监控：按配置查询多个商品 SKU 和门店，有货时通过 [Bark](https://github.com/Finb/Bark) 推送到 iPhone。

从原来的单款 iPhone / 固定三家门店脚本整理而来。商品类别没有硬编码：iPhone、Mac、iPad、Apple Watch、AirPods、AirTag、配件都可以配置。**实际可用范围是「美国 Apple Store、提供到店取货接口的具体 SKU」**，不是自动扫描苹果全品类，也不保证所有商品都能查询。

不需要 Apple ID、浏览器或大模型。Apple 请求使用 [Scrapling](https://github.com/D4Vinci/Scrapling)；不启用浏览器自动化、验证码处理或代理轮换。仅查询和提醒，不包含购物袋、自动下单或支付代码。

## 快速开始

需要 Python **3.10+** 和可访问 Apple 美国商店、Bark 服务的网络。macOS / Linux / Windows 均可在终端运行；本次只在 macOS 验证。

```bash
git clone https://github.com/poi233/apple-stock-bark.git
cd apple-stock-bark
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Windows PowerShell 用 `.venv\Scripts\Activate.ps1` 激活环境，用 `Copy-Item config.example.json config.json` 复制配置；`python3` 不存在时使用 `python`。

先修改 `config.json` 的商品、SKU、邮编、门店，再检查配置：

```bash
python monitor.py --check-config
python monitor.py --once
```

`--check-config` 仅校验配置，不联网。`--once` 查询一轮、打印结果，**不发 Bark**。退出码：`0` 全部无货；`10` 至少一项有货；`2` 配置、网络、接口或解析错误。多商品一轮只要发生错误，退出码就是 `2`，即使其他商品已查到有货。Shell 把 `10` 当作非零退出码是正常的。

配置 Bark 后启动持续监控。下方交互式读取避免把密钥直接写进 shell 命令历史：

```bash
# bash / zsh
printf 'Bark base URL (https://api.day.app/your-key): '
read -r -s BARK_URL
printf '\n'
export BARK_URL
python monitor.py
```

PowerShell 可用 `$env:BARK_URL = Read-Host 'Bark base URL'` 设置当前进程环境；输入会显示在终端，请注意屏幕隐私。

Bark URL 格式为 `https://api.day.app/<你的设备密钥>`，也支持同样格式的自托管 HTTPS Bark 域名。填基础地址，不要粘贴标题、正文、查询参数。密钥只通过 `BARK_URL` 读取，不放进 JSON、代码或 Git。

启动监控不会发送测试推送；第一次查到有货会通知。如确实需要测试手机通知，请主动运行：

```bash
python monitor.py --test-notify
```

这个命令会真实发送一条 Bark 推送。测试套件不会联网或发送推送。停止持续监控用 `Ctrl+C`。

## 配置

### iPhone 18 Pro Max 512GB 冰川蓝示例

完整配置见 [config.iphone18-pm.example.json](config.iphone18-pm.example.json)：美国无锁版，SKU `MJWC4LL/A`，邮编 `97204`，监控 Portland 的 Pioneer Place（R077）、Washington Square（R090）和 Bridgeport Village（R134）。每轮结束等待 120 秒，持续有货每 900 秒再次提醒。

```bash
python monitor.py --config config.iphone18-pm.example.json --check-config
python monitor.py --config config.iphone18-pm.example.json --once
# 设置 BARK_URL 后，持续监控并推送
python monitor.py --config config.iphone18-pm.example.json
```

这个 SKU 和商品链接沿用原私人监控脚本；本次只校验配置格式，未重新验证该手机的当前商品页、SKU 对应规格或库存。运行前请在 Apple 页面核对；其他国家或地区的同名手机不可直接复用美国 SKU。

### 默认 AirTag 示例与字段

示例里的 SKU 来自 2026-10-17 Apple 美国 AirTag 页面，R077 在当次匿名查询中返回 `Available Today`。SKU 和库存都会变化，使用前必须自行核对当前页面、包装数量和地区。示例不是购买建议。

| 字段 | 含义 |
| --- | --- |
| `storefront` | 当前只接受 `us`；其他地区会报错 |
| `postal_code` | 美国邮编，须和目标地区相符，由 Apple 确认 |
| `stores` | 门店 ID 到显示名称的映射，例如 `R077` |
| `products` | 一个或多个商品对象，每个包含 `name`、`part`、`url` |
| `name` | 提醒中的商品名称；脚本不会校验名称是否对应 SKU |
| `part` | Apple 完整零件号 / SKU，含地区后缀，例如 `MFE94LL/A` |
| `url` | `https://www.apple.com/shop/` 下的有效商品页或购买页，作为会话预热和通知跳转链接 |
| `interval_seconds` | 一轮结束后的等待秒数，最少 60，示例 120 |
| `reminder_seconds` | 同一 SKU、同一门店持续有货的再次提醒间隔，最少 60，示例 900 |

给 `products` 数组增加对象即可监控另一款产品；同一型号不同颜色、容量、尺寸、网络制式或包装数通常有不同 SKU，逐项配置。给 `stores` 增加 ID 即可查询多家店。所有商品共享同一个邮编和门店集合；不同地区请分开配置，并注意多个进程共享出口 IP 的总请求量。

使用其他配置文件：

```bash
python monitor.py --config config.other.local.json --check-config
python monitor.py --config config.other.local.json --once
```

### 怎么找到 SKU 和门店 ID

在 Apple 美国商店选择完整规格，查看商品页的零件号或链接中的 `/shop/product/<零件号>/…`。iPhone / Mac 等选配页面不一定在 URL 中展示 SKU；可用浏览器开发者工具的 Network，在正常使用网站的到店取货查询中查看 `product` 参数和门店 `storeId`。不要把序列号、营销型号或 `Axxxx` 设备型号当作 SKU。

使用当前可打开且不重定向的购买页作为 `url`。脚本不跟随重定向；下架商品可能跳到搜索页，应更新链接和 SKU。通用购买页可以用于预热，但它不证明 SKU 正确。

不提供自动抓取全站目录、自动推导 SKU、店名搜索或商品规格匹配。Apple 接口返回无效 / 空响应时会报告失败，不会猜测规格或自动更换商品。

## 通知和频率

- 每个商品、门店分别去重；第一次有货推送，持续有货按提醒间隔再推送。确认无货后，下一次补货重新通知。通知状态只保存在内存，重启会重新提醒。
- 一轮内串行查询商品，相邻商品查询间至少等待 60 秒，单次查询的一家或多家门店在同一个请求中提交。N 个商品一轮约耗时 `(N−1)×60 秒 + 网络耗时`，然后再等待 `interval_seconds`。`--once` 同样遵守这个间隔。
- Bark 失败不会标记为已通知，后续查询仍有货时再尝试。服务超时可能意味着消息已经送达，因此偶尔可能重复；Bark 返回成功只证明服务接收，不保证 APNs 或手机已显示。
- HTTP 重定向、403、429、541、异常结构和未知状态均视为错误；不会当成无货。接口错误会中止当前轮，持续模式进行全局退避，最长通常 900 秒，并尊重服务的数值 `Retry-After`。不会绕过登录、验证码或封锁。
- Bark 使用 `timeSensitive` 等级。是否响铃、振动或显示，受 Bark、iOS 通知权限、专注模式、网络及 APNs 状态影响。没有旧私用脚本的洛杉矶静音时段或 critical 强提醒设置。

## 限制与使用边界

1. **「支持产品类别」不等于全商品覆盖。** 只查询配置的 SKU。软件、服务、礼品卡、维修、企业订单、翻新商店、教育商店、定制 Mac、刻字、运营商限定款及不支持店取的商品不保证适用；没有线上到店取货数据的商品无法通过这个接口监控。
2. **目前只有美国商店和英文状态。** 中国大陆、香港、日本、欧洲等商店的域名、地址流程和语言各不相同，不能仅改邮编或域名就声称支持。未知文案直接报错，需补充真实响应和测试后再扩展。
3. **使用非公开、无稳定承诺的 Apple 网页接口。** `/shop/address/location/update` 和 `/shop/sba/pickup-detail` 可能随时变更、限流或停用；HTTP 200 也可能是空响应或挑战页。脚本无法保证长期可用。
4. **库存是一次快照。** `Available Today`、`Available Tomorrow` 或将来可取日期均算「可店取」，不表示一定有现货、可立即拿走或已预留。通知后库存可能已售完，购物袋也不会锁定库存。以结账页为准。
5. **有漏报窗口。** 每分钟级轮询可能错过短暂补货；多商品越多，每个 SKU 的再次查询越慢。不要通过多开进程、高频查询、代理轮换来规避限制。看到持续 403/429/541 后应停止并等待。
6. **电脑必须保持运行和联网。** 终端关闭、机器休眠、正常合盖睡眠都会停止或延迟监控；macOS 可在同一终端使用 `caffeinate -i python monitor.py` 防止空闲休眠，但它不保证合盖后运行。此仓库没有安装 LaunchAgent、cron 或系统服务。GitHub Actions 不作为实时库存监控宿主。
7. **只提醒，不抢购。** 不登录 Apple ID、不读密码、不触发结账、不购买。商品名和 SKU 是否匹配由使用者核对。
8. **隐私。** Apple 收到邮编、SKU、目标门店和出口 IP；Bark 服务收到设备密钥、商品名、门店及跳转 URL。共享终端环境、进程环境和第三方日志也可能暴露密钥。仓库忽略 `config.json`、`.env*`、`.bark-url` 和日志，但不要依赖 `.gitignore` 清理已经提交过的密钥。
9. **规则会变化。** 尊重 Apple 的服务条款、robots 规则、授权和速率限制。2026-10-17 实测 robots 允许所用路径；脚本不自动刷新或执行 robots 规则，运行前应自行确认当前政策。无需也不提供反爬绕过机制。

## 开发与验收

```bash
python -m unittest -v
python monitor.py --config config.example.json --check-config
python monitor.py --config config.example.json --once
```

前两条是离线校验；最后一条会真实请求 Apple，但不发送 Bark。真实 SKU 有货时预期退出 `10`。

本次整理的验收记录及来源见 [VALIDATION.md](VALIDATION.md)。仓库不包含原来机器上的 Bark 密钥、个人配置、日志、订单记录和自动结账代码。
