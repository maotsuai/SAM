#!/system/bin/sh
. "/data/adb/modules/SAM/scripts/base.sh"

if [ "$1" = "--packages-namespace" ]; then
    . "${SCRIPTS_PATH}/compat/packages.sh"
    umask 077
    sam_mount_packages_view "$2" /data/system/packages.list /data/system/packages.xml \
        /proc/self/ns/mnt /proc/self/mountinfo || {
        log "e" "Mihomo 应用列表兼容模式初始化失败，已停止启动"
        exit 1
    }
    log "i" "Mihomo 使用独立应用列表快照: ${SAM_PACKAGES_SNAPSHOT}"
else
    case "${MIHOMO_PACKAGE_COMPAT:-false}" in
        true)
            parent_ns=$(readlink /proc/self/ns/mnt) || exit 1
            # 只隔离挂载；网络命名空间保持不变，TUN 路由仍作用于手机。
            exec busybox unshare -m /system/bin/sh "$0" --packages-namespace "$parent_ns"
            ;;
        false) ;;
        *)
            log "e" "MIHOMO_PACKAGE_COMPAT 只接受 true 或 false"
            exit 1
            ;;
    esac
fi

exec busybox setuidgid "${SAM_USER}:${TUN_GROUP}" "${MIHOMO_BIN}" -d "${MIHOMO_PATH}"
