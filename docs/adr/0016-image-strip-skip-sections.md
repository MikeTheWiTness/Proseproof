# ADR-0016: 图像清洗 + 板块跳过 —— 从硬编码到 CLI/配置驱动

- **状态**: 已采纳
- **日期**: 2026-07-04

## 背景

两个共享模块实现了有用的功能，但完全硬编码了垂直领域（教育）的规则，无法被通用用户使用：

**`decor_utils.py`**（47 行）：
- `strip_decor_images()` 硬编码匹配 `image1.png`/`image2.png` 文件名模式
- 意图：剥离 pandoc 转换后残留的装饰性小图标
- 从未被 import 到转换或校对管线

**`split_post_utils.py`**（51 行）：
- `remove_navigation_units()` 硬编码匹配 `直击课堂|本讲导航` 板块标题
- 意图：拆分后删除导航页/封面板块
- 从未被调用

两个模块的核心理念都是合理的（转换时清洗、拆分后清理），但实现方式不可配置。

## 决策

### 1. 图像清洗：CLI 驱动（替代 `decor_utils.py`）

给 `proseproof convert` 命令新增三个互斥选项：

| 选项 | 行为 |
|------|------|
| `--no-images` | 剥离所有 Markdown 图片引用 `![](...)` |
| `--strip-small-images` | 自动过滤文件大小 < 5KB 的图片（常见于装饰性小图标） |
| `--strip-images-below N` | 用户指定字节阈值 |

默认不开启任何图像清洗。

挂在 `convert` 阶段而非 `proofread` 阶段，因为这是格式转换时的清洗，不应影响校对逻辑。

### 2. 板块跳过：配置驱动（替代 `split_post_utils.py`）

在 `config.json` 的 `lecture_split` 中增加 `skip_sections` 字段：

```json
"lecture_split": {
  "mode": "title",
  "skip_sections": ["直击课堂", "本讲导航"]
}
```

- `skip_sections`：正则模式列表（字符串，自动编译）
- 拆分后扫描每个片段目录的 `.md` 文件首行
- 命中任一模式的目录被整树删除
- 默认值 `[]`（不跳过任何板块）

挂在 `BaseProfile._write_fragments_to_dirs()` 末尾，不影响其他分割模式。

## 后果

**正面**:
- 两个硬编码模块被删除（合计 ~100 行），核心能力保留
- 通用用户默认不受影响（选项默认关闭/空列表）
- 教育领域用户通过配置文件获得同等能力

**负面**:
- `--strip-small-images` 需要访问文件系统获取图片大小，在 `convert` 阶段需要图片已落盘
- `skip_sections` 依赖正则——用户需要了解基本的正则语法
