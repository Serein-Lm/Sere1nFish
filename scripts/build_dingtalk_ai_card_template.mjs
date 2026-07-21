#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const [, , inputArg, outputArg] = process.argv;

if (!inputArg || !outputArg) {
  console.error(
    "用法: node scripts/build_dingtalk_ai_card_template.mjs <钉钉导出.json> <输出.json>",
  );
  process.exit(2);
}

const inputPath = path.resolve(inputArg);
const outputPath = path.resolve(outputArg);
const exported = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const editor = JSON.parse(exported.editorData);

function descendants(node) {
  const result = [];
  const visit = (value) => {
    if (Array.isArray(value)) {
      for (const child of value) visit(child);
      return;
    }
    if (!value || typeof value !== "object") return;
    if (value.componentName) result.push(value);
    for (const child of value.children || []) visit(child);
  };
  visit(node);
  return result;
}

function findAll(node, componentName) {
  return descendants(node).filter((item) => item.componentName === componentName);
}

function dynamicValue(value) {
  return value && typeof value === "object" ? value.value : value;
}

function setBaseTextStyle(component, { token, size, lineHeight, bold, maxLines }) {
  const props = component.props;
  props.fontColorType = "Standard";
  props.color = {
    type: "dynamicColor",
    valueType: "fixed",
    value: token,
    variable: "",
    variableType: "global",
  };
  props.fontSizeType = "Standard";
  props.styleType = "custom";
  props.customFontSize = size;
  props.customFontLineHeight = lineHeight;
  props.bold = bold;
  props.maxLine = {
    type: "dynamicNumber",
    valueType: "fixed",
    value: maxLines,
    variable: "",
    variableType: "global",
  };
  props.widthMode = "match_parent";
  props.autoWidth = false;
  props.autoMaxWidth = false;
}

function setGridWidth(component) {
  const props = component.props;
  props.width = 100;
  props.height = 36;
  props.isAutoWidth = false;
  props.isAutoHeight = true;
  props.isFixedWidth = false;
  props.childGravity = "leftCenter";
}

function elementRange(xml, userId) {
  const marker = `userId="${userId}"`;
  const markerIndex = xml.indexOf(marker);
  if (markerIndex < 0) return null;
  const start = xml.lastIndexOf("<", markerIndex);
  const openEnd = xml.indexOf(">", markerIndex) + 1;
  const openTag = xml.slice(start, openEnd);
  const tagMatch = openTag.match(/^<([A-Za-z0-9_:-]+)/);
  if (!tagMatch) throw new Error(`无法识别组件 ${userId} 的 Widget 标签`);
  if (/\/\s*>$/.test(openTag)) return { start, end: openEnd, openEnd };

  const tag = tagMatch[1];
  const tagPattern = new RegExp(`<\/?${tag}\\b[^>]*>`, "g");
  tagPattern.lastIndex = start;
  let depth = 0;
  let match;
  while ((match = tagPattern.exec(xml))) {
    const value = match[0];
    if (value.startsWith(`</${tag}`)) {
      depth -= 1;
      if (depth === 0) {
        return { start, end: tagPattern.lastIndex, openEnd };
      }
    } else if (!/\/\s*>$/.test(value)) {
      depth += 1;
    }
  }
  throw new Error(`组件 ${userId} 的 Widget 标签未闭合`);
}

function updateElement(xml, userId, updater) {
  const range = elementRange(xml, userId);
  if (!range) throw new Error(`WidgetInfo 缺少组件 ${userId}`);
  const block = xml.slice(range.start, range.end);
  return xml.slice(0, range.start) + updater(block) + xml.slice(range.end);
}

function removeElement(xml, userId) {
  const range = elementRange(xml, userId);
  if (!range) return xml;
  return xml.slice(0, range.start) + xml.slice(range.end);
}

function setAttribute(openTag, name, value) {
  const pattern = new RegExp(`(\\s${name})="[^"]*"`);
  if (pattern.test(openTag)) return openTag.replace(pattern, `$1="${value}"`);
  return openTag.replace(/>$/, `\n  ${name}="${value}">`);
}

function removeAttribute(openTag, name) {
  return openTag.replace(new RegExp(`\\s${name}="[^"]*"`, "g"), "");
}

function updateOpeningTag(block, updater) {
  const end = block.indexOf(">") + 1;
  return updater(block.slice(0, end)) + block.slice(end);
}

function styleWidgetText(block, { size, lineHeight, bold, maxLines, color }) {
  return block.replace(/<FastTextView\b[^>]*>/, (original) => {
    let open = setAttribute(original, "maxLines", String(maxLines));
    open = setAttribute(open, "textSize", `${size}np`);
    open = setAttribute(open, "lineHeight", `${lineHeight}np`);
    open = setAttribute(open, "isBold", String(bold));
    return setAttribute(open, "textColor", color);
  });
}

function removeWidgetTag(block, tagName) {
  const pattern = new RegExp(
    `\\s*<${tagName}\\b[\\s\\S]*?<\\/${tagName}>`,
    "g",
  );
  return block.replace(pattern, "");
}

const componentTree = editor.schema?.componentsTree || [];
const statusContainers = findAll(componentTree, "AICardStatusContainer").filter(
  (item) => [2, 3].includes(Number(dynamicValue(item.props?.status))),
);

if (statusContainers.length !== 2) {
  throw new Error("模板必须同时包含输出中状态和完成状态");
}

const cardContainer = findAll(componentTree, "AICardContainer")[0];
if (!cardContainer) throw new Error("模板缺少 AICardContainer");
cardContainer.props.enableGradientBorder = false;

const removals = [];
const compactRows = [];
const queryTexts = [];
const dividers = [];

for (const status of statusContainers) {
  const statusValue = Number(dynamicValue(status.props?.status));
  const content = (status.children || []).find(
    (item) => item.componentName === "AICardContent",
  );
  if (!content) throw new Error(`状态 ${status.props.status} 缺少 AICardContent`);

  const children = content.children || [];
  const query = children.find(
    (item) =>
      item.componentName === "BaseText" &&
      item.props?.text?.content === "${query}",
  );
  const preparationLoop = children.find(
    (item) =>
      item.componentName === "Loop" &&
      item.props?.listData?.variable === "preparations",
  );
  const markdown = children.find((item) => item.componentName === "MarkdownBlock");
  const allDividers = children.filter((item) => item.componentName === "Divider");
  const chartLoop = children.find(
    (item) =>
      item.componentName === "Loop" && item.props?.listData?.variable === "charts",
  );

  if (!query || !markdown || allDividers.length < 1) {
    throw new Error(`状态 ${status.props.status} 的标准 AI Card 结构不完整`);
  }

  setBaseTextStyle(query, {
    token: "common_level1_base_color",
    size: 14,
    lineHeight: 20,
    bold: true,
    maxLines: 3,
  });
  query.props.enableIcon = false;
  query.props.marginTop = 12;
  query.props.marginBottom = statusValue === 2 ? 2 : 0;

  const divider = allDividers[0];
  divider.props.marginLeft = 12;
  divider.props.marginRight = 12;
  divider.props.marginTop = 8;
  divider.props.marginBottom = 8;
  dividers.push(divider.id);

  let keptChildren;
  if (statusValue === 2) {
    if (!preparationLoop) {
      throw new Error("输出中状态缺少 preparations 循环");
    }
    const outerGrid = (preparationLoop.children || []).find(
      (item) => item.componentName === "Grid",
    );
    const rowText = outerGrid
      ? findAll(outerGrid, "BaseText").find(
          (item) => item.props?.text?.content === "${loop.name}",
        )
      : null;
    const progressGrid = outerGrid
      ? (outerGrid.children || []).find(
          (item) => findAll(item, "ProgressBar").length > 0,
        )
      : null;
    const labelGrid = outerGrid
      ? (outerGrid.children || []).find(
          (item) => findAll(item, "BaseText").includes(rowText),
        )
      : null;

    if (!outerGrid || !labelGrid || !rowText) {
      throw new Error("输出中状态的进度行结构不完整");
    }
    if (progressGrid) {
      outerGrid.children = outerGrid.children.filter((item) => item !== progressGrid);
      removals.push(progressGrid.id);
    }

    preparationLoop.props.width = 100;
    preparationLoop.props.height = 28;
    preparationLoop.props.childWidth = "match_parent";
    preparationLoop.props.childGap = false;
    preparationLoop.props.flowLayout = false;
    preparationLoop.props.isFixedHeight = false;

    setGridWidth(outerGrid);
    outerGrid.props.height = 28;
    outerGrid.props.direction = "vertical";
    outerGrid.props.hasGradientBackground = false;
    outerGrid.props.hasBackground = false;
    outerGrid.props.marginLeft = 0;
    outerGrid.props.marginRight = 0;

    setGridWidth(labelGrid);
    labelGrid.props.height = 28;
    labelGrid.props.paddingLeft.value = 0;
    labelGrid.props.paddingRight.value = 0;

    setBaseTextStyle(rowText, {
      token: "common_blue1_color",
      size: 12,
      lineHeight: 18,
      bold: false,
      maxLines: 1,
    });
    rowText.props.marginTop = 2;
    rowText.props.marginBottom = 2;
    rowText.props.marginLeft = 0;
    rowText.props.marginRight = 0;

    compactRows.push({
      outerGrid: outerGrid.id,
      labelGrid: labelGrid.id,
      text: rowText.id,
    });
    keptChildren = [query, preparationLoop, divider, markdown];
  } else {
    keptChildren = [query, divider, markdown];
  }

  content.children = keptChildren;
  for (const removed of children.filter((item) => !content.children.includes(item))) {
    removals.push(removed.id);
  }

  queryTexts.push({ id: query.id, marginBottom: statusValue === 2 ? 2 : 0 });
  if (chartLoop) removals.push(chartLoop.id);
}

const pending = findAll(componentTree, "AIPending")[0];
if (pending?.props?.pendingTip) {
  pending.props.pendingTip.content = "AI 中枢正在处理";
}

let widgetInfo = String(exported.widgetInfo || "");
for (const userId of [...new Set(removals.filter(Boolean))]) {
  widgetInfo = removeElement(widgetInfo, userId);
}

const activityText =
  "@dtDarkModeAdapter{&#039;#1677FF&#039;,&#039;#6EA8FF&#039;}";
const primaryText =
  "@dtDarkModeAdapter{&#039;#172B4D&#039;,&#039;#F4F6F8&#039;}";

const animationId = `${cardContainer.id}_animation`;
if (widgetInfo.includes(`userId="${animationId}"`)) {
  widgetInfo = updateElement(widgetInfo, animationId, (source) => {
    let block = removeWidgetTag(source, "DDCSSStyleView");
    block = removeWidgetTag(block, "DDAnimationView");
    return updateOpeningTag(block, (original) => {
      let open = removeAttribute(original, "backgroundGradient");
      return setAttribute(
        open,
        "backgroundColor",
        "@dtDarkModeAdapter{&#039;#FFFFFF&#039;,&#039;#1E1E1E&#039;}",
      );
    });
  });
}

for (const row of compactRows) {
  widgetInfo = updateElement(widgetInfo, row.outerGrid, (block) =>
    updateOpeningTag(block, (original) => {
      let open = removeAttribute(original, "backgroundGradient");
      open = removeAttribute(open, "backgroundColor");
      open = setAttribute(open, "orientation", "vertical");
      open = setAttribute(open, "childGravity", "leftCenter");
      open = setAttribute(open, "marginLeft", "0np");
      open = setAttribute(open, "marginRight", "0np");
      open = setAttribute(open, "paddingLeft", "12np");
      return setAttribute(open, "paddingRight", "12np");
    }),
  );
  widgetInfo = updateElement(widgetInfo, row.labelGrid, (block) =>
    updateOpeningTag(block, (original) => {
      let open = setAttribute(original, "childGravity", "leftCenter");
      open = setAttribute(open, "paddingLeft", "0np");
      return setAttribute(open, "paddingRight", "0np");
    }),
  );
  widgetInfo = updateElement(widgetInfo, row.text, (block) => {
    const withMargins = updateOpeningTag(block, (original) => {
      let open = setAttribute(original, "marginTop", "2np");
      return setAttribute(open, "marginBottom", "2np");
    });
    return styleWidgetText(withMargins, {
      size: 12,
      lineHeight: 18,
      bold: "false",
      maxLines: 1,
      color: activityText,
    });
  });
}

for (const query of queryTexts) {
  widgetInfo = updateElement(widgetInfo, query.id, (source) => {
    const withoutIcon = removeWidgetTag(source, "DDIconView");
    const withMargins = updateOpeningTag(withoutIcon, (original) => {
      let open = setAttribute(original, "marginTop", "12np");
      return setAttribute(open, "marginBottom", `${query.marginBottom}np`);
    });
    return styleWidgetText(withMargins, {
      size: 14,
      lineHeight: 20,
      bold: "true",
      maxLines: 3,
      color: primaryText,
    });
  });
}

for (const userId of dividers) {
  widgetInfo = updateElement(widgetInfo, userId, (block) =>
    updateOpeningTag(block, (original) => {
      let open = setAttribute(original, "marginLeft", "12np");
      open = setAttribute(open, "marginRight", "12np");
      open = setAttribute(open, "marginTop", "8np");
      return setAttribute(open, "marginBottom", "8np");
    }),
  );
}

if (pending?.id && widgetInfo.includes(`userId="${pending.id}"`)) {
  widgetInfo = updateElement(widgetInfo, pending.id, (block) =>
    block.replace(/text="处理中\.\.\."/, 'text="AI 中枢正在处理"'),
  );
}

if (widgetInfo.includes("@subdata{&#039;progress&#039;}")) {
  throw new Error("响应式 WidgetInfo 中仍存在百分比进度条");
}

exported.editorData = JSON.stringify(editor);
exported.widgetInfo = widgetInfo;
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(exported, null, 2)}\n`, "utf8");

console.log(`已生成响应式 AI Card: ${outputPath}`);
