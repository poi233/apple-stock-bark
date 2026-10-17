# 验收记录

日期：2026-10-17（America/Los_Angeles）。代码只做库存查询与 Bark 提醒。

## 本地验证

在独立虚拟环境安装 `requirements.txt`：Python 3.12.11，Scrapling 0.4.15。

```text
python -m unittest -v
Ran 14 tests in 0.003s
OK

python monitor.py --config config.example.json --check-config
Config valid
exit: 0

python monitor.py --config config.example.json --once
AirTag (verify your exact pack / SKU before using): Pioneer Place
exit: 10
```

最后一项真实请求了 Apple：购买页、邮编确认与库存接口均返回 HTTP 200，邮编确认成功；当前 AirTag SKU `MFE94LL/A` 在 `R077` 返回 `Available Today`。这只证明该时间点、该 SKU 和门店的匿名查询可用，不证明其他产品、其他地区或未来持续可用。

旧 Siri Remote `MW5G3AM/A` 的历史商品页返回 HTTP 301，库存接口返回空 `body`。这类响应作为错误处理，不能证明无货。

离线测试覆盖：配置类型和 URL 边界、重复 SKU、英文状态识别、缺失/重复门店与异常响应、会话与邮编确认、多个产品、只查模式不发送 Bark、通知冷却与补货复位、发送失败后的再次尝试、Bark JSON 请求和错误码、HTTP 429 的全局暂停与长 `Retry-After`。

本次没有读取原项目的 Bark 密钥，也没有实际发送 Bark 测试消息。Bark 请求格式与失败分支通过模拟验证；手机实际接收、通知权限和 APNs 链路未验证。没有启动长期监控、安装后台服务或执行任何购买。

## 来源与发布范围

库存流程从用户原有的私人脚本中提炼；原脚本说明接口思路参考 [Golfrey/apple-stock](https://github.com/Golfrey/apple-stock)。本仓库重新实现可配置的监控部分，未带入私人 checkout、订单、日志或凭据文件。

本仓库尚未指定独立的开源许可证；公开可见不代表授予任意再分发许可。依赖项目及相关服务各自的许可证、条款继续适用。
