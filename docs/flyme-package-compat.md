# Flyme 应用列表解析兼容方案（实验性）

本方案针对 [mihomo #1362 中的 Flyme 讨论](https://github.com/MetaCubeX/mihomo/issues/1362#issuecomment-2210431930)：

```text
Start TUN listening error: build android rules: read packages list:
invalid interned reference: 65280, exists: 36
```

这是应用数据库解析失败，与该 issue 前半段的 IPv6 地址绑定失败是两条不同路径。
尚未取得报错设备的数据库样本，不能断言 Flyme 具体改变了哪种编码，也不能推断所有魅族机型都受影响。

## SAM 的相关结构

分析基于上游 SAM `7d8ea46`。它组合 SmartDNS、AdGuardHome 和 mihomo，按设置选择 DNS 服务顺序。
实际透明代理入口是 TUN：`base.yaml` 默认启用 `gvisor`、`auto-route`，由 mihomo 设置策略路由。
虽然声明了 redir/tproxy 监听端口，模块的 `scripts/iptables/mihomo.sh` 只给 TUN 添加 FORWARD 放行规则，未实现完整 TProxy 接管链。

启动依次执行 `scripts/update/mihomo.sh` 生成配置、`scripts/update/proxy.sh` 添加
`include-package` / `exclude-package`，再以 `root:net_admin` 启动核心。
默认还启用 `find-process-mode: always`。所以 SAM 有与该讨论相同的应用列表解析入口；是否在具体设备触发，需要实机日志确认。

仓库内核心的 Go 构建信息报告 mihomo 修订 `ab405bad5beeeac8b003bb01f60f134f6df54471`，
依赖 `sing-tun v0.4.24`，同时带有 dirty 标记。以下源码是对应修订的公开源码参照，不能据此证明二进制没有额外修改。

调用链如下：

1. [mihomo `buildAndroidRules`](https://github.com/MetaCubeX/mihomo/blob/ab405bad5beeeac8b003bb01f60f134f6df54471/listener/sing_tun/server_android.go)
   无条件调用 `getPackageManager()`，并调用其 `Start()`。
2. [sing-tun `updatePackages`](https://github.com/MetaCubeX/sing-tun/blob/v0.4.24/packages_android.go)
   读取 `/data/system/packages.xml`，识别为 ABX 后使用 sing 的 ABX reader，否则使用标准 XML decoder。
3. ABX reader 返回解析错误，向上导致 TUN 初始化失败；核心其他服务仍可能继续运行。
4. 包名解析也复用这个 PackageManager，所以运行中的应用识别涉及同一份数据库。

因此，这个核心仅改成 UID 黑白名单或 `find-process-mode: off`，仍不足以跳过 TUN 初始化时的数据库读取。
直接删除包名规则也不能保证启动成功。反复重启无法修复稳定复现的格式解析错误。

## 本补丁的做法

新增 `MIHOMO_PACKAGE_COMPAT`，默认 `false`。启用后：

1. 以 `busybox unshare -m` 创建仅供 mihomo 使用的挂载命名空间。
2. 比较父子命名空间 ID，设置递归 private，并检查 mountinfo 中不存在 shared 挂载。
   任一步失败都退出，不继续执行 bind mount。
3. 从 `/data/system/packages.list` 的包名和 appId 两列生成最小标准 XML。
   [AOSP 写入代码](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/pm/Settings.java)
   描述了这种文本格式。输入为空、ID 越界、名称不安全或同名 ID 冲突时退出。
4. 仅在该命名空间将快照 bind 到 `/data/system/packages.xml`，再设为只读。
   核心从原有路径读到普通 XML，从而绕开 ABX reader。
5. 用原来的用户组执行原版 mihomo；网络命名空间保持原样，核心仍管理手机的 TUN 路由。

这个实现不向系统数据库写入内容。私有 bind 挂载随进程及其命名空间释放。
快照只保存包名和 appId，权限由 `umask 077` 限制，临时文件由下一次模块启动清理。
无需维护定制核心；升级后的核心如果改变数据库路径或解析逻辑，仍需重新验证。

另一个改动是启动检查：最多等待 10 秒，只有核心存在且指定 TUN 网卡出现，才添加转发规则。
超时则终止本次启动的进程并记录失败。它能识别“核心活着但 TUN 未创建”的情况，不能替代端到端联网检查。

## 使用与回退

在安装了本分支的模块中，编辑 `/data/adb/modules/SAM/setting.conf`：

```sh
MIHOMO_PACKAGE_COMPAT=true
```

填写订阅后重启模块：

```sh
su -c /data/adb/modules/SAM/scripts/service.sh restart
```

检查 `/data/adb/modules/SAM/tmp/mihomo.log` 和 `module.log`，应看到使用独立应用列表快照的消息，
随后 TUN 网卡创建成功。若出现 `unshare`、`mount` 或 SELinux 拒绝，兼容模式会停止启动；日志保留具体错误。
设备需提供 BusyBox 的 unshare、mount、awk、mktemp、setuidgid，并允许 root 创建挂载命名空间。

回退时将开关改回 `false`，再重启模块即可。开关在模块覆盖安装时会保留。

## 已知边界

- 快照在启动时生成。安装、卸载应用或 UID 变化后，必须重启模块；原数据库的文件更新不会刷新这个私有快照。
- 普通包名黑白名单仍交由 sing-tun 按 appId 转换，用户空间 ID 的扩展也仍由它负责。
  这依赖设备提供标准格式的 `packages.list`，并非对 Flyme 应用列表格式作全面兼容。
- 多个应用共用 UID 时，路由无法按其中一个包名单独隔离；反向识别可能显示同 UID 的另一个包名。
  `packages.list` 不包含 `android.uid.system` 一类 shared-user 别名，本方案不恢复这些别名；
  依赖 shared-user 名称或精确进程名称的规则需要单独验证，不能保证等同原数据库。
- 独立挂载空间不能解决 TUN 内核支持、IPv6 绑定、SELinux 策略或其他路由错误。
  本分支没有修改这些设置。

## 验证情况与实机验收

离线验证：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
sh -n scripts/compat/packages.sh scripts/service/mihomo-launch.sh
bash -n scripts/service/mihomo.sh customize.sh setting.conf
git diff --check
```

测试覆盖 XML 转换、非法/空输入、共享 UID、同名冲突、父子命名空间保护、shared 挂载拒绝、
挂载失败中止、默认启动路径、unshare 失败不继续启动，以及进程存活但没有 TUN 的情况。
命名空间和 mount 使用模拟命令；未在 Linux 内核或魅族实机执行真实挂载、TUN 与联网测试。

实机验收应包含：

1. 同一设备保存关闭兼容模式时的原始报错，开启后确认 `ip link show dev Meta` 成功。
2. 从普通 root 会话读取系统 `packages.xml`，从 `/proc/<mihomo-pid>/root/data/system/packages.xml`
   读取核心看到的文件，确认前者仍为原数据库，后者为生成的 XML。
3. 验证黑名单、白名单两个模式的普通应用，以及 TCP、UDP、DNS、IPv6、多用户/应用分身的实际行为。
4. 安装新应用，重启模块后验证映射更新；停止服务、关闭兼容模式后验证挂载视图释放。

若要长期在核心层解决，更直接的方向是让 sing-tun 在 ABX 解码失败时回退到可靠的应用查询接口或文本列表，
并保留共享用户和更新通知语义。那需要独立维护/提交核心补丁。切换全套 TProxy 则需要补齐双栈策略路由、
UDP 接管、UID 排除和防回环规则，改动明显大于本模块级绕过方案。
