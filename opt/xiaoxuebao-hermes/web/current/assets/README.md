# 小雪宝首页 — 视觉资产使用说明

## 图片替换

所有页面使用的图片存放在 `public/assets/` 目录下：

| 文件名 | 用途 | 推荐格式 | 建议尺寸 |
|--------|------|----------|----------|
| `mascot.png` | Hero 区小雪宝吉祥物形象 | PNG（透明背景） | 600x700px+ |
| `scene-warm.png` | 项目介绍区场景图 | PNG / WebP | 800x600px+ |
| `banner.png` | 横幅宣传图（备用） | PNG | 1200x400px+ |
| `scene-family.png` | 家庭护理场景漫画 | PNG | 800x600px+ |
| `scene-doctor.png` | 医生使用场景漫画 | PNG | 800x600px+ |
| `bright.png` | 明亮风格宣传图 | PNG | 600x600px+ |
| `warm-magic.png` | 温暖魔法绘本封面 | PNG | 600x800px+ |

替换图片时只需将新文件放入 `public/assets/` 目录，保持相同文件名即可。
如需更改文件名，请同时修改对应组件中的 `src` 属性。

## 团队信息更新

团队成员数据在 `src/data/content.ts` 中的 `teamMembers` 数组。

每个成员包含：
- `name` — 姓名
- `role` — 角色
- `bio` — 一句话介绍
- `avatar`（可选）— 头像图片路径

如需添加头像，将图片放入 `public/assets/` 目录，然后在 `avatar` 字段中填写路径（如 `/assets/avatar-name.png`）。

## 颜色调整

全局颜色 token 定义在 `src/index.css` 的 `:root` 中。
修改 seed token 即可全局更换配色方案。
