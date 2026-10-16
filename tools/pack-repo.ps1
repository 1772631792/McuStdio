$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    npx repomix --ignore "artifacts/**,runtime/**,data/**,Debug/**,Release/**,*.o,*.hex,*.axf,*.bin,*.map,*.obj,*.d"
}
finally {
    Pop-Location
}
