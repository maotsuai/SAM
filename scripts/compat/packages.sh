# 只供 mihomo-launch.sh 在新建的挂载命名空间中调用。
# 参数显式传入路径，便于在无 root 的测试环境验证保护条件。
sam_mount_packages_view() {
    local parent_ns="$1" source_list="$2" target_xml="$3"
    local ns_path="$4" mountinfo="$5" current_ns snapshot_dir snapshot

    current_ns=$(readlink "$ns_path") || return 1
    if [ -z "$parent_ns" ] || [ -z "$current_ns" ] || [ "$parent_ns" = "$current_ns" ]; then
        echo "SAM: 未进入独立挂载命名空间，拒绝挂载应用列表" >&2
        return 1
    fi
    if [ ! -r "$source_list" ] || [ ! -f "$target_xml" ] || [ -L "$target_xml" ]; then
        echo "SAM: 无法读取 packages.list 或 packages.xml 目标无效" >&2
        return 1
    fi

    # 新命名空间仍可能继承 shared 挂载，必须先切断向外传播。
    busybox mount --make-rprivate / || return 1
    if ! busybox awk '
        /(^| )shared:[0-9]+( |$)/ { unsafe = 1 }
        END { exit unsafe || NR == 0 }
    ' "$mountinfo"; then
        echo "SAM: 挂载仍可能向外传播，拒绝挂载应用列表" >&2
        return 1
    fi

    snapshot_dir=$(busybox mktemp -d "${TMP_PATH}/mihomo-packages.XXXXXX") || return 1
    snapshot="${snapshot_dir}/packages.xml"
    if ! busybox awk -f "${SCRIPTS_PATH}/compat/packages-list.awk" "$source_list" > "$snapshot"; then
        rm -rf "$snapshot_dir"
        return 1
    fi
    if ! busybox mount --bind "$snapshot" "$target_xml"; then
        rm -rf "$snapshot_dir"
        return 1
    fi
    if ! busybox mount -o remount,bind,ro "$snapshot" "$target_xml"; then
        busybox umount "$target_xml"
        rm -rf "$snapshot_dir"
        return 1
    fi
    # 快照随本次 mihomo 进程使用，下次模块启动时由 tmp 清理流程删除。
    SAM_PACKAGES_SNAPSHOT="$snapshot"
}
