# 加载基础脚本
source "/data/adb/modules/SAM/scripts/base.sh"

# 启动
start(){ 
    # 判断 Mihomo 未启用，则退出
    if [ ${MIHOMO_ENABLE} = false ]; then
        log "e" "${MIHOMO_BIN} 未启用"
        return 1
    fi
    
    if [ ${#SUB_URL[@]} = 0 ]; then
        log "e" "未填写订阅地址，无法启动 ${MIHOMO_BIN}"
        return 1
    fi        
       
    # 判断 Mihomo 已运行，则退出
    isRun ${MIHOMO_BIN} && {
        log "e" "${MIHOMO_BIN} 已运行"
        return 1
    }
    
    # 更新 Mihomo 配置
    ${UPDATE_PATH}/mihomo.sh
    # 更新代理名单
    ${UPDATE_PATH}/proxy.sh
    
    # 启动
    "${SERVICE_PATH}/mihomo-launch.sh" > "${TMP_PATH}/${MIHOMO_BIN}.log" 2>&1 &
    launch_pid=$!

    # TUN 初始化失败时核心仍可能存活，不能只根据 PID 宣布启动成功。
    attempt=0
    while [ "$attempt" -lt 10 ]; do
        sleep 1
        kill -0 "$launch_pid" 2>/dev/null || break
        if isRun "${MIHOMO_BIN}" && ip link show dev "${TUN_DEVICE}" >/dev/null 2>&1; then
            log "i" "${MIHOMO_BIN} 和 TUN 网卡启动成功"
            ${IPTABLES_PATH}/mihomo.sh add
            return 0
        fi
        attempt=$((attempt + 1))
    done
    kill "$launch_pid" 2>/dev/null
    log "e" "${MIHOMO_BIN} 或 TUN 网卡启动失败，请检查 ${TMP_PATH}/${MIHOMO_BIN}.log"
    return 1
}

# 停止
stop(){
    # 判断 Mihomo 未运行，则退出
    isRun ${MIHOMO_BIN} || {
        log "e" "${MIHOMO_BIN} 已停止"
        return 1
    }
    # 杀死 Mihomo 进程
    kill_process ${MIHOMO_BIN} && log "i" "${MIHOMO_BIN} 停止成功" || log "e" "${MIHOMO_BIN} 停止失败"
}

# 添加指令
case "$1" in
    # 启动
    start)
        start
        ;;
    # 停止
    stop)
        stop
        ;;
esac
