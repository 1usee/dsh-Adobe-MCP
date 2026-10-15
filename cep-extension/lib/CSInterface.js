/*
 * =====================================================================
 * cep-extension/lib/CSInterface.js
 * ---------------------------------------------------------------------
 * 【占位文件 —— 请用 Adobe 官方副本覆盖它】
 *
 * Adobe 官方的 `CSInterface.js` 是随 CEP 扩展一起分发的库（定义类
 * `CSInterface`，并在内部使用宿主注入的 `window.__adobe_cep__`）。
 * 本仓库在**离线环境**下无法取得该文件的官方副本，因此这里放一个空占位，
 * 作用是：
 *
 *   1. 让 index.html 里 `<script src="lib/CSInterface.js">` 这个**部署槽位**
 *      真实存在，不产生 404，也不让"缺少文件"掩盖真正的问题；
 *   2. 明确告诉部署者：把这个文件替换成官方副本即可启用官方库。
 *
 * 【本文件不定义 window.CSInterface】
 *   刻意留空不定义任何构造函数，因此加载它不会覆盖任何东西。
 *   index.html 的加载顺序是：
 *     1) 本文件（若已被官方副本覆盖，则定义 window.CSInterface）
 *     2) lib/csinterface-shim.js（降级实现，仅在无 window.CSInterface 时兜底）
 *     3) js/main.js（面板引导）
 *   于是：
 *     - 换成官方库 → 走官方实现；
 *     - 保持本占位 → 走 csinterface-shim.js；
 *     - 真实 CEP 宿主内 → 无论上面哪条，js/main.js 都会优先用宿主注入的
 *       window.__adobe_cep__ 构造 CSInterface（真实环境里它一定存在）。
 *
 * 【部署方法】
 *   把官方 CSInterface.js 的内容整个覆盖到本文件（路径与文件名保持不变），
 *   或改成 `<script src="lib/CSInterface.js">` 指向你自己放置的官方库。
 *   详见 README「部署」与 lib/csinterface-shim.js 的文件头说明。
 *
 * 【与授权无关】
 *   本文件不含、也不读取任何宿主授权/许可信息（说明书 §1.3）。
 * =====================================================================
 */
