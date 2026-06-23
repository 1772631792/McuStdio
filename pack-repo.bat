# 1. 确保终端走你的本地代理通道（防止卡死）
$env:HTTP_PROXY="http://127.0.0.1:7890"; $env:HTTPS_PROXY="http://127.0.0.1:7890"

# 2. 直接原生打包（排除掉你工程里常见的嵌入式编译杂质）
npx repomix --ignore "build/**,Debug/**,Release/**,*.o,*.hex,*.axf,*.bin,*.map,*.obj,*.d"