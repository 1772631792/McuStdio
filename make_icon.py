# make_icon.py
import os
from PIL import Image, ImageDraw

def create_mcu_studio_icon():
    # 创建一个 256x256 的高清图标画布，带透明通道
    size = (256, 256)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    # 1. 绘制科技暗黑底座（圆角矩形）
    # 边缘稍微留白，防止被系统裁剪
    draw.rounded_rectangle([12, 12, 244, 244], radius=45, fill="#121212", outline="#1e88e5", width=4)
    
    # 2. 绘制深色微调网格背景（示波器质感）
    for i in range(40, 220, 30):
        # 横向网格线
        draw.line([(16, i), (240, i)], fill="#222222", width=1)
        # 纵向网格线
        draw.line([(i, 16), (i, 240)], fill="#222222", width=1)
        
    # 3. 绘制芯片核心 (MCU 剪影)
    # 芯片主体
    draw.rectangle([78, 78, 178, 178], fill="#1e1e1e", outline="#90a4ae", width=3)
    # 芯片引脚 (Pins)
    for offset in range(90, 170, 20):
        # 上引脚
        draw.line([(offset, 64), (offset, 78)], fill="#90a4ae", width=3)
        # 下引脚
        draw.line([(offset, 178), (offset, 192)], fill="#90a4ae", width=3)
        # 左引脚
        draw.line([(64, offset), (78, offset)], fill="#90a4ae", width=3)
        # 右引脚
        draw.line([(178, offset), (192, offset)], fill="#90a4ae", width=3)

    # 4. 绘制穿越芯片的高速绿光脉冲波形 (CH1 标志性波形)
    wave_points = [
        (24, 180), (60, 180), (85, 110), (110, 210), 
        (135, 50), (160, 190), (185, 130), (232, 130)
    ]
    # 绘制外发光层（半透明绿色）
    draw.line(wave_points, fill="#00E676", width=5, joint="round")
    # 绘制内核心线（纯白高亮，极具极客电子感）
    draw.line(wave_points, fill="#FFFFFF", width=2, joint="round")

    # 5. 保存为 Windows 标准的高清多尺寸图标文件
    icon_path = "mcu_studio.ico"
    image.save(icon_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"⚡ 帅气的 MCU Studio 图标已成功生成在: {os.path.abspath(icon_path)}")

if __name__ == "__main__":
    # 如果提示缺少 PIL 库，请先执行: pip install Pillow
    try:
        create_mcu_studio_icon()
    except ImportError:
        print("❌ 检测到缺少 Pillow 库，正在自动为你安装...")
        os.system("pip install Pillow")
        create_mcu_studio_icon()