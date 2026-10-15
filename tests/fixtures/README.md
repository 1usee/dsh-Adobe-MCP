# 测试素材（fixtures）

集成测试与 `scripts/smoke.py` 需要一小段真实素材。仓库**不提交二进制素材**，
由脚本在本机生成。

## 生成方式

需要本机有 `ffmpeg`（任意较新版本）：

```powershell
pwsh -File tests\fixtures\make_fixtures.ps1
```

生成结果（`tests/fixtures/media/`，已在 `.gitignore` 中忽略）：

| 文件 | 内容 | 用途 |
|---|---|---|
| `bars_5s_1080p25.mp4` | 5 秒彩条 + 1kHz 正弦音，1920×1080@25fps，H.264/AAC | 导入、建序列、上时间线、导出 |
| `bars_2s_720p25.mp4` | 2 秒彩条，1280×720@25fps | 覆盖"不同规格素材"的分支 |

素材刻意做得很小（每个 < 1MB），以便导入与导出都很快。

## 手工等价命令

```powershell
ffmpeg -hide_banner -y -f lavfi -i "smptebars=size=1920x1080:rate=25:duration=5" `
       -f lavfi -i "sine=frequency=1000:duration=5" `
       -c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 28 `
       -c:a aac -b:a 96k -shortest tests\fixtures\media\bars_5s_1080p25.mp4
```

## 为什么不做成 pytest fixture 自动生成

生成素材依赖一个外部可执行文件（ffmpeg），把它塞进测试前置会让"跑单元测试"
变成"先装 ffmpeg"。因此：素材缺失时集成测试会**明确跳过并给出生成命令**，
而不是悄悄用一个替代品让测试"看起来通过"。
