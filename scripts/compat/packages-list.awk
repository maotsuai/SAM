# packages.list 的前两列是包名和 appId；不读取 ROM 的 ABX 数据库。
BEGIN { print "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<packages>" }
/^[[:space:]]*$/ { next }
{
    if ($1 !~ /^[A-Za-z0-9_][A-Za-z0-9_.]*$/ ||
        $2 !~ /^[0-9]+$/ || $2 + 0 <= 0 || $2 + 0 >= 100000) {
        print "SAM: packages.list 包名或 appId 无效，行 " NR > "/dev/stderr"
        failed = 1
        exit 1
    }
    app_id = sprintf("%.0f", $2 + 0)
    if ($1 in ids) {
        if (ids[$1] != app_id) {
            print "SAM: packages.list 同一包名的 appId 冲突，行 " NR > "/dev/stderr"
            failed = 1
            exit 1
        }
        next
    }
    ids[$1] = app_id
    count++
    printf "  <package name=\"%s\" userId=\"%s\"/>\n", $1, app_id
}
END {
    print "</packages>"
    if (failed || count == 0) {
        if (count == 0) print "SAM: packages.list 没有有效应用" > "/dev/stderr"
        exit 1
    }
}
