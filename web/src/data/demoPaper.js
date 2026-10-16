// Verbatim excerpts: © 2022 Jeganathan et al., CC BY 4.0.
// https://doi.org/10.1371/journal.pone.0266052
// Checked against the publisher's full text on 2026-10-12. Chinese reading
// notes, translations and questions below are editorial demonstration content.
export const demoPaper = {
  title: 'Impact of smoking status and chronic obstructive pulmonary disease on pulmonary complications post lung cancer surgery',
  titleZh: '吸烟、COPD 与肺癌术后肺部并发症',
  authors: 'Jeganathan V, Knight S, Bricknell M, et al.',
  citation: 'Jeganathan V, Knight S, Bricknell M, et al. (2022). Impact of smoking status and chronic obstructive pulmonary disease on pulmonary complications post lung cancer surgery. PLOS ONE 17(3): e0266052.',
  url: 'https://doi.org/10.1371/journal.pone.0266052',
  license: 'https://creativecommons.org/licenses/by/4.0/',
  sections: [
    {
      id: 'design', number: '01', label: '看清设计', source: 'Methods · 首段', kind: '方法卡',
      question: '前瞻性收集的数据，为什么是回顾性研究？',
      quote: 'A retrospective review was undertaken of a prospectively maintained database of consecutive operations performed for lung cancer resection.',
      translation: '研究对一个前瞻性维护的数据库进行了回顾性分析，数据库连续收录肺癌切除术病例。',
      lead: '先分清数据怎么来，再判断研究怎么做。',
      explanation: '数据库在临床工作中持续记录，研究者随后回看已有资料，分析吸烟状态、COPD 与术后并发症的关联。数据的收集方式与这次研究的设计，要分别判断。',
      takeaway: '前瞻性维护数据库，不等于本次研究采用前瞻性试验设计。这是一项基于既有数据库的回顾性分析。',
      context: '研究纳入 1995—2018 年接受肺叶切除或全肺切除的 1,013 例患者。此信息见 Methods · Population 和 Results · Baseline characteristics。',
      prompts: [
        { question: '怎样判断是不是随机试验？', answer: '看研究者是否随机分配干预。本文按已有吸烟状态和 COPD 状态比较结局，没有随机分组；不能从“前瞻性数据库”推导出随机试验。' },
        { question: '这句话记进卡片有什么用？', answer: '以后读到 registry、database 或 prospectively collected data，可以先问：是在描述数据来源，还是本次研究的设计？这一判断决定后续能作多强的因果解释。' },
      ],
    },
    {
      id: 'result', number: '02', label: '读准数字', source: 'Results · 术后并发症段落', kind: '发现卡',
      question: '27% 和 17%，差的是 10% 吗？',
      quote: 'PPCs were more frequent in current smokers compared to never smokers (27% vs 17%, p = 0.036)',
      translation: '当前吸烟者的术后肺部并发症比例高于从不吸烟者：27% 对 17%，p = 0.036。',
      lead: '先认清比较对象，再解释差值。',
      explanation: '这里比较的是当前吸烟者与从不吸烟者。两组报告比例相差 10 个百分点；“百分点”表示比例的绝对差，和相对变化百分比不同。',
      takeaway: '当前吸烟者与从不吸烟者的术后肺部并发症比例为 27% 与 17%，绝对差为 10 个百分点。这是组间观察到的关联。',
      context: 'PPCs 指术后肺部并发症。当前吸烟者与既往吸烟者的比较是另一项比较，不能把这两个组别混在一起。',
      prompts: [
        { question: '能写成“吸烟导致增加 10 个百分点”吗？', answer: '这份比较支持“观察到更高比例”。要解释为吸烟造成的因果差值，还需要处理混杂、选择偏倚等问题；回顾性组间比较本身不足以完成这个推断。' },
        { question: '为什么不只记 p 值？', answer: 'p 值没有交代比较了谁、差多少。卡片同时保留组别、27% 与 17%、差值单位以及原文位置，之后用于汇报时才有完整上下文。' },
      ],
    },
    {
      id: 'limits', number: '03', label: '追问边界', source: 'Discussion · limitations 第三项', kind: '批判卡',
      question: '哪些没有记录的信息，会改变解释？',
      quote: 'The third limitation is that we do not have data on pre-existing comorbidities that may have increased the post-operative complication rate.',
      translation: '第三项局限是缺少既往合并症数据，而这些合并症可能增加术后并发症比例。',
      lead: '让局限对应一个具体的解释问题。',
      explanation: '作者明确指出缺少既往合并症资料。阅读时应继续追问：组间基础健康状况是否不同？模型已经调整了哪些变量，还遗漏了哪些可能影响结局的因素？',
      takeaway: '研究缺少既往合并症数据。解释吸烟、COPD 与术后结局的关联时，需要保留未测量混杂这一局限。',
      context: '本文统计方法描述调整年龄、性别与 BMI。调整了这些变量，仍不能补回数据库没有记录的合并症。',
      prompts: [
        { question: '怎么把这条局限用于自己的研究？', answer: '设计资料收集表时，把主要暴露、结局以及预计影响两者关系的合并症一起列入。先决定需要控制哪些混杂，再核对数据是否真的具备。' },
        { question: '引用时该保留什么限定？', answer: '说明这是一项特定手术人群中的观察性研究，并记录缺少既往合并症数据这一局限。引用应保持论文的人群、结局与设计范围。' },
      ],
    },
  ],
}

export const DEMO_STORAGE_KEY = 'litfinch-demo-cards-v1'

export function readDemoCards() {
  try {
    const saved = JSON.parse(localStorage.getItem(DEMO_STORAGE_KEY))
    return Array.isArray(saved) ? demoPaper.sections.filter(section => saved.includes(section.id)).map(section => section.id) : []
  } catch { return [] }
}

export function demoMarkdown(ids) {
  const cards = demoPaper.sections.filter(section => ids.includes(section.id))
  return [
    '# 研雀试读 · 阅读卡片', '',
    '预设解读演示；原文节选按 CC BY 4.0 使用。中文翻译、解读与卡片由研雀整理，不属于原论文文字。', '',
    demoPaper.citation, demoPaper.url, `原文许可：${demoPaper.license}`, '',
    ...cards.flatMap(section => [
      `## ${section.kind} · ${section.question}`, '', section.takeaway, '',
      `原文位置：${section.source}`, '', `> ${section.quote}`, '',
    ]),
  ].join('\n')
}
