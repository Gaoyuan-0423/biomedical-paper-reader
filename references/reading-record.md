# 逐图阅读记录与完成检查

有图的阅读任务在材料盘点时建立此记录，随后复用并更新。它补充已有主张索引，不再新建平行科学证据表。完整精读要求已有各主图、补图及 Extended Data 至少概览；深入核查的面板由主张、对照和待解问题决定。速读／专题只要求所选图及必要依赖，但列清未读项。

## 状态与实际动作

| 状态 | 要记录的事实 | 不能据此声称 |
|---|---|---|
| `pending` | 图号、来源、是否在范围内，以及尚未查看的原因 | 已读图、图中显示、图像已核查 |
| `caption_only` | 已读图注位置；图像仍未看 | 视觉确认、由图像发现的冲突 |
| `overview` | 成功查看记录与简短观察；图号、布局和总体比较可辨认 | 所有面板、精确数字都已核查 |
| `panels` | 概览记录、实际核查的面板及其标签／方向／数值观察 | 未列面板也已核查 |
| `unreadable` | 具体来源或工具障碍、已尝试的可用替代方式及剩余限制 | 已查看或已验证；把单纯未打开称为读不清 |

转换、复制、裁剪、保存或在报告中嵌入图片，都不构成查看动作。只有图像实际返回给读者并观察后才推进状态。失败调用不能算查看成功。整图不清时可读取原图或 PDF 页；没有更清晰来源则限定具体主张，不猜图。

先按正文、完整图注及可用文件盘点图号；图注 PDF 可能只有文字，不能当作补图图像。清单与 Methods／Results 引用核对，不能仅列准备看过的几张。每个核心主张关联相关主图、补图、方法和必要对照；QC、批次校正、细胞注释及阴性结果同样影响解释。

## 轻量记录格式

有文件工具时在论文工作目录维护 `reading-record.json`；无文件工具时用等价紧凑表，并在对话中检查实际调用。不要求安装数据库、固定图像工具或依赖库。图像来源、裁图与 PDF 页序需可追溯；源版本改变时回查受影响项。

JSON 使用以下字段。`figures` 列盘点到的全部图；`required` 在速读／专题中表示选择的图及必要依赖，完整精读不能用 `false` 跳过已有图。

```json
{
  "schema_version": 1,
  "mode": "full",
  "delivery_status": "complete",
  "inventory_complete": true,
  "figures": [
    {
      "id": "Fig. 1",
      "role": "main",
      "source": "main.pdf",
      "status": "panels",
      "required": true,
      "view_event_ids": ["view-01"],
      "observation": "第4页整图A–C；B的横轴是患者组，纵轴为评分，红组高于蓝组。",
      "checked_panels": ["B"],
      "limitation": ""
    }
  ],
  "claims": [
    {
      "id": "E1",
      "kind": "result",
      "figure_ids": ["Fig. 1"],
      "basis": "image",
      "status": "supported",
      "comparison_checked": true,
      "verification_note": "B的分组、轴定义与相关方法一致；仅核查图示方向。"
    }
  ],
  "report_assertions": [
    {
      "figure_id": "Fig. 1",
      "level": "panels",
      "report_location": "逐图解读：Fig. 1B"
    }
  ]
}
```

`mode` 为 `full`／`quick`／`focused`，`delivery_status` 为 `complete`／`limited`。未完成图像步骤的记录用 `limited`，不能仅保留默认 `complete`。`inventory_complete` 只表示已完成实际材料盘点，不表示阅读完成。

`claims` 复用报告证据编号：`kind` 为 `result`／`criticism`／`recommendation`，`basis` 为 `text`／`image`，`status` 为 `supported`／`unresolved`／`conflict`。仅据正文或图注的主张用 `text`。已确认冲突需要比较条件核对与复核说明；比较身份未确定用 `unresolved`。字段完整不证明判断正确，科学含义仍按[证据规则](evidence-rules.md#图像含义与冲突确认)复核。

`report_assertions` 对应报告所有“图像已看／已核查”的逐图声明及汇总所计图号。它是检查报告与记录的桥梁；不能为校验通过而漏列报告中实际存在的声明。汇总概览数计 `overview` 与 `panels`，面板核查数只计 `panels`；`caption_only` 不进入视觉计数。

## 查看记录与校验

查看事件引用来自本次实际调用的标识或稳定顺序编号，并与源文件／页／裁图关联。宿主提供原始调用日志时，从成功的图像返回事件提取校验输入；保留原日志位置便于复查。无需重放图片或向外部上传文件。

标准化事件 JSONL 每行一个事件：

```json
{"event_id":"view-01","kind":"image_view","success":true,"figure_ids":["Fig. 1"]}
```

只有返回图像给读者的真实动作使用 `image_view`；转换或复制分别使用 `convert`／`copy`。同一次返回确实包含多个完整图时可关联多个图号。转换出的文件路径与图号需先对应，不能把任意成功事件绑定成看过另一张图。标准化事件须来自真实调用结果；填写者自报的事件不能冒充独立导出的宿主日志。

有 Python 标准库环境时使用[记录校验器](../scripts/check_reading_record.py)：

```bash
python3 <skill目录>/scripts/check_reading_record.py reading-record.json
python3 <skill目录>/scripts/check_reading_record.py reading-record.json --events image-events.jsonl
```

无事件文件时只检查记录和声明一致性，不能证明原始工具动作。提供真实事件文件时另外检查成功图像返回、图号匹配与虚报动作；仍不能证明读图的科学判断正确。没有 Python 时按相同条件人工对照，不因此停止已获授权的阅读。

## 写作与交付条件

- 完整精读：清单盘点完成，已有各图均已概览，必要面板和关键批评已核查／复核，记录与报告声明一致。少数面板无法读清时具体限定相关数字，不把概览写成逐面板确认。
- 速读／专题：所选图及必要依赖已查看，准备引用的数字和批评有相应核查；未选图不替其背书。不能因图组未选就跳过目标结论所需对照。
- 有范围内可读图待看：继续读取。上下文不足时保存记录分批续做，不以已有若干发现作为完成依据。
- 来源、格式或工具障碍确实无法解决：标 `limited`，在开篇、相关主张和资源状态中说明具体未完成项与判断边界；一致性检查通过不代表完整精读完成。用户明确缩小范围时同步模式／范围，不悄然缩小后继续声称全部核验。
- 记录与报告不一致、未查看却作视觉判断、已确认冲突未核对比较条件：修正或继续核查后再交付。文件存在、链接有效、报告篇幅足够均不能替代这些条件。

阅读记录与科学批评一起更新：复核发现读错标签、不同统计层级或不同比较时，撤回相应冲突，同步开篇冲突数量、阅读建议、证据表及局限。不要仅改文末附件表。
