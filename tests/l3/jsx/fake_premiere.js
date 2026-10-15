"use strict";
/*
 * tests/l3/jsx/fake_premiere.js —— Premiere Pro DOM 的最小伪实现
 *
 * 目的：让 tests/l3/jsx/run.js 能在 Node 里把 ops/*.jsx 的真实逻辑跑起来，
 * 包括"写后读回校验"、"参数形式变体"、"QE 兜底"这些真实存在的分支。
 *
 * 设计原则：
 *   1. 只实现处理器**实际用到**的 API（照 ppro-scripting 文档的形状实现）；
 *   2. 所有会改变宿主状态的操作都 bump()（计数器 + 日志），这样 dry-run 测试
 *      可以严格断言"DOM 未被改动"；
 *   3. 故意制造几处"版本差异"：setPlayerPosition 只吃 tick 字符串、
 *      setInPoint 只吃秒/Time、insertClip 不吃 Time 对象——用来验证
 *      我们那套"多形式尝试 + 读回校验"确实在工作，而不是被伪实现惯着。
 */

const fs = require("fs");
const path = require("path");
const os = require("os");

const TPS = 254016000000;

/* ------------------------------------------------------------------ 时间 */

function Time(seconds) {
  this._ticks = "0";
  if (typeof seconds === "number") {
    this.seconds = seconds;
  }
}

Object.defineProperty(Time.prototype, "ticks", {
  get: function () { return this._ticks; },
  set: function (value) { this._ticks = String(Math.round(Number(value))); }
});

Object.defineProperty(Time.prototype, "seconds", {
  get: function () { return Number(this._ticks) / TPS; },
  set: function (value) { this._ticks = String(Math.round(Number(value) * TPS)); }
});

Time.prototype.setSecondsAsFraction = function (numerator, denominator) {
  if (!denominator) { throw new Error("setSecondsAsFraction: 分母为 0"); }
  this._ticks = String(Math.round((numerator / denominator) * TPS));
  return true;
};

Time.prototype.getFormatted = function () { return String(this.seconds); };

/* -------------------------------------------------------------- 文件系统 */

function File(filePath) {
  this.fsName = String(filePath);
  this.encoding = "UTF-8";
  this._openPath = null;
}

Object.defineProperty(File.prototype, "exists", {
  get: function () { try { return fs.existsSync(this.fsName); } catch (error) { return false; } }
});

Object.defineProperty(File.prototype, "parent", {
  get: function () { return new Folder(path.dirname(this.fsName)); }
});

File.prototype.open = function () { this._openPath = this.fsName; return true; };
File.prototype.read = function () {
  if (!this._openPath) { return ""; }
  try { return fs.readFileSync(this._openPath, "utf8"); } catch (error) { return ""; }
};
File.prototype.close = function () { this._openPath = null; return true; };

function Folder(folderPath) { this.fsName = String(folderPath); }

Object.defineProperty(Folder.prototype, "exists", {
  get: function () {
    try { return fs.statSync(this.fsName).isDirectory(); } catch (error) { return false; }
  }
});

/* -------------------------------------------------------- 集合（快照形式） */

function snapshotCollection(items, countNames) {
  const collection = {};
  items.forEach(function (item, index) { collection[index] = item; });
  countNames.forEach(function (name) { collection[name] = items.length; });
  return collection;
}

/* -------------------------------------------------------------------- 标记 */

class Marker {
  constructor(seconds) {
    this.name = "";
    this.comments = "";
    this.type = "Comment";
    this.guid = "marker-" + Math.random().toString(16).slice(2);
    this._start = new Time(seconds);
    this._end = new Time(seconds);
  }

  get start() { return this._start; }
  set start(value) { this._start = toTime(value); }

  get end() { return this._end; }
  // 文档：设置 marker.end 时传"秒数"，不是完整 Time 对象
  set end(value) { this._end = toTime(value); }

  setTypeAsComment() { this.type = "Comment"; return 0; }
  setTypeAsChapter() { this.type = "Chapter"; return 0; }
  setTypeAsSegmentation() { this.type = "Segmentation"; return 0; }
  setTypeAsWebLink() { this.type = "WebLink"; return 0; }
}

function toTime(value) {
  if (value instanceof Time) { return value; }
  if (value && typeof value.seconds === "number") { return new Time(value.seconds); }
  return new Time(Number(value));
}

class MarkerCollection {
  constructor() { this._markers = []; }

  get numMarkers() { return this._markers.length; }

  createMarker(seconds) {
    const marker = new Marker(seconds);
    this._markers.push(marker);
    this._markers.sort(function (a, b) { return a.start.seconds - b.start.seconds; });
    return marker;
  }

  getFirstMarker() { return this._markers[0]; }

  getNextMarker(current) {
    const index = this._markers.indexOf(current);
    return index >= 0 ? this._markers[index + 1] : undefined;
  }

  deleteMarker(marker) {
    const index = this._markers.indexOf(marker);
    if (index >= 0) { this._markers.splice(index, 1); return true; }
    return false;
  }
}

/* ------------------------------------------------------------------ 效果 */

class FakeComponentParam {
  constructor(displayName, matchName, value, timeVarying) {
    this.displayName = displayName;
    this.matchName = matchName;
    this._value = value;
    this._timeVarying = timeVarying === true;
  }

  getValue() {
    if (this._timeVarying) { throw new Error("getValue() 不能用于时间变化的参数"); }
    return this._value;
  }

  setValue(value, updateUI) {
    if (this._timeVarying) { throw new Error("setValue() 不能用于时间变化的参数"); }
    if (typeof value !== typeof this._value && typeof value !== "number") {
      throw new Error("参数类型不匹配");
    }
    this._value = value;
    return 0;
  }

  isTimeVarying() { return this._timeVarying; }
  areKeyframesSupported() { return true; }
  getKeys() { return 0; }
}

class FakeComponent {
  constructor(displayName, matchName, params) {
    this.displayName = displayName;
    this.matchName = matchName;
    this._params = params || [];
  }

  get properties() { return snapshotCollection(this._params, ["numItems"]); }
}

/* ---------------------------------------------------------------- TrackItem */

class TrackItem {
  constructor(projectItem, startSeconds, durationSeconds, mediaType, fake) {
    this._fake = fake;
    this.projectItem = projectItem;
    this.name = projectItem ? projectItem.name : "clip";
    this.mediaType = mediaType;
    this.type = mediaType === "Video" ? 1 : 2;
    this.nodeId = "trackitem-" + Math.random().toString(16).slice(2);
    this.start = new Time(startSeconds);
    this.duration = new Time(durationSeconds);
    this.end = new Time(startSeconds + durationSeconds);
    this.inPoint = new Time(0);
    this.outPoint = new Time(durationSeconds);
    this.disabled = false;
    this._selected = false;
    this._speed = 1;
    this._components = [];
    this._track = null;
  }

  get components() { return snapshotCollection(this._components, ["numItems"]); }

  addComponent(component) { this._components.push(component); }

  isSelected() { return this._selected; }
  setSelected(state) { this._selected = state === 1 || state === true; return 0; }
  getSpeed() { return this._speed; }
  isAdjustmentLayer() { return false; }

  remove(ripple, alignToVideo) {
    const track = this._track;
    if (!track) { throw new Error("TrackItem 不属于任何轨道"); }
    const list = track._clips;
    const index = list.indexOf(this);
    if (index < 0) { throw new Error("片段不在轨道上"); }
    list.splice(index, 1);
    const removedStart = this.start.seconds;
    const removedDuration = this.end.seconds - removedStart;
    if (ripple) {
      list.forEach(function (other) {
        if (other.start.seconds > removedStart) {
          other.start = new Time(other.start.seconds - removedDuration);
          other.end = new Time(other.end.seconds - removedDuration);
        }
      });
    }
    this._fake.bump("trackItem.remove");
    this._track._fake.syncSequence(this._track);
    return 0;
  }

  // 文档：move(newInPoint) 按"位移量"移动（Time 对象）
  move(newInPoint) {
    const delta = (newInPoint && typeof newInPoint.seconds === "number")
      ? newInPoint.seconds
      : Number(newInPoint);
    this.start = new Time(this.start.seconds + delta);
    this.end = new Time(this.end.seconds + delta);
    this._fake.bump("trackItem.move");
    this._track._fake.syncSequence(this._track);
    return 0;
  }
}

/* ------------------------------------------------------------------- Track */

class Track {
  constructor(name, mediaType, fake) {
    this.name = name;
    this.mediaType = mediaType;
    this.id = Track._nextId++;
    this._fake = fake;
    this._clips = [];
    this._transitions = [];
    this._muted = false;
    this._sequence = null;
  }

  get clips() { return snapshotCollection(this._clips, ["numItems"]); }
  get transitions() { return snapshotCollection(this._transitions, ["numItems"]); }

  isMuted() { return this._muted; }
  setMute(isMuted) { this._muted = isMuted === 1 || isMuted === true; this._fake.bump("track.setMute"); return 0; }

  insertClip(projectItem, time, vTrackIndex, aTrackIndex) {
    if (time instanceof Time) {
      throw new Error("insertClip 的 time 参数应为 tick 字符串");
    }
    if (!projectItem || typeof projectItem.getMediaPath !== "function"
        || projectItem.isBin()) {
      throw new Error("insertClip 需要媒体素材（不能是素材箱/序列）");
    }
    const startSeconds = coerceSeconds(time);
    const duration = projectItem._durationSeconds || 5;
    const item = new TrackItem(projectItem, startSeconds, duration, this.mediaType, this._fake);
    item._track = this;
    this._clips.push(item);
    this._sort();
    this._fake.bump("track.insertClip");
    this._fake.syncSequence(this);
    return undefined;
  }

  overwriteClip(projectItem, time) {
    if (!projectItem || typeof projectItem.getMediaPath !== "function"
        || projectItem.isBin()) {
      throw new Error("overwriteClip 需要媒体素材（不能是素材箱/序列）");
    }
    const startSeconds = coerceSeconds(time);
    const duration = projectItem._durationSeconds || 5;
    const range = [startSeconds, startSeconds + duration];
    this._clips = this._clips.filter(function (clip) {
      return clip.end.seconds <= range[0] || clip.start.seconds >= range[1];
    });
    const item = new TrackItem(projectItem, startSeconds, duration, this.mediaType, this._fake);
    item._track = this;
    this._clips.push(item);
    this._sort();
    this._fake.bump("track.overwriteClip");
    this._fake.syncSequence(this);
    return true;
  }

  _sort() {
    this._clips.sort(function (a, b) { return a.start.seconds - b.start.seconds; });
  }
}

Track._nextId = 1;

function coerceSeconds(value) {
  if (value instanceof Time) { return value.seconds; }
  if (typeof value === "string") { return Number(value) / TPS; }
  if (typeof value === "number") { return value; }
  throw new Error("无法把 " + String(value) + " 解释为时间");
}

/* ---------------------------------------------------------------- Sequence */

class Sequence {
  constructor(name, fake) {
    this._fake = fake;
    this.name = name;
    this.sequenceID = "seq-" + Math.random().toString(16).slice(2);
    this.projectItem = null;
    this._videoTracks = [new Track("V1", "Video", fake), new Track("V2", "Video", fake)];
    this._audioTracks = [new Track("A1", "Audio", fake)];
    this._videoTracks.concat(this._audioTracks).forEach((track) => { track._sequence = this; });
    this.markers = new MarkerCollection();
    this.end = "0";
    this.timebase = String(Math.round(TPS / 25));
    this.zeroPoint = "0";
    this.frameSizeHorizontal = 1920;
    this.frameSizeVertical = 1080;
    this._inPoint = 0;
    this._outPoint = 0;
    this._workStart = 0;
    this._workEnd = 0;
    this._playheadTicks = "0";
  }

  get videoTracks() { return snapshotCollection(this._videoTracks, ["numTracks", "numItems"]); }
  get audioTracks() { return snapshotCollection(this._audioTracks, ["numTracks", "numItems"]); }

  getInPoint() { return String(this._inPoint); }
  setInPoint(value) { this._inPoint = coerceSeconds(value); this._fake.bump("sequence.setInPoint"); return 0; }
  getOutPoint() { return String(this._outPoint); }
  setOutPoint(value) { this._outPoint = coerceSeconds(value); this._fake.bump("sequence.setOutPoint"); return 0; }
  getInPointAsTime() { return new Time(this._inPoint); }
  getOutPointAsTime() { return new Time(this._outPoint); }

  getPlayerPosition() { return new Time(Number(this._playheadTicks) / TPS); }
  // 故意只接受 tick 字符串：验证"多形式尝试 + 读回校验"确实能走到第二种形式
  setPlayerPosition(value) {
    if (typeof value !== "string") {
      throw new Error("setPlayerPosition 需要 tick 字符串（该宿主版本不接受 Time 对象）");
    }
    this._playheadTicks = String(Math.round(Number(value)));
    this._fake.bump("sequence.setPlayerPosition");
    return 0;
  }

  getWorkAreaInPoint() { return String(this._workStart); }
  setWorkAreaInPoint(value) { this._workStart = coerceSeconds(value); this._fake.bump("sequence.setWorkAreaInPoint"); return 0; }
  getWorkAreaOutPoint() { return String(this._workEnd); }
  setWorkAreaOutPoint(value) { this._workEnd = coerceSeconds(value); this._fake.bump("sequence.setWorkAreaOutPoint"); return 0; }

  getSelection() {
    const selected = [];
    this._allTracks().forEach(function (track) {
      track._clips.forEach(function (clip) { if (clip.isSelected()) { selected.push(clip); } });
    });
    return snapshotCollection(selected, ["numItems"]);
  }

  getSettings() {
    return {
      videoFrameWidth: this.frameSizeHorizontal,
      videoFrameHeight: this.frameSizeVertical,
      videoFrameRate: TPS / Number(this.timebase),
      audioChannelCount: 2
    };
  }

  _allTracks() { return this._videoTracks.concat(this._audioTracks); }

  _recalcEnd() {
    let end = 0;
    this._allTracks().forEach(function (track) {
      track._clips.forEach(function (clip) { if (clip.end.seconds > end) { end = clip.end.seconds; } });
    });
    this.end = String(Math.round(end * TPS));
  }
}

/* ----------------------------------------------------------------- Project */

class ProjectItem {
  constructor(name, type, options) {
    const opts = options || {};
    this.name = name;
    this.type = type;                       // "BIN" | "CLIP" | "FILE" | "ROOT"
    this.nodeId = "node-" + (++ProjectItem._counter);
    this._parent = null;
    this._children = [];
    this._mediaPath = opts.mediaPath || null;
    this._offline = opts.offline === true;
    this._sequence = opts.sequence || null;
    this._proxyPath = null;
    this._durationSeconds = opts.durationSeconds || 5;
    this._frameRate = opts.frameRate || 25;
    this._hasVideo = opts.hasVideo !== false;
    this._hasAudio = opts.hasAudio !== false;
    this._markers = new MarkerCollection();
  }

  get children() { return snapshotCollection(this._children, ["numItems"]); }

  get treePath() {
    const parts = [];
    let node = this;
    while (node._parent) { parts.unshift(node.name); node = node._parent; }
    return "\\\\" + node.name + (parts.length ? "\\" + parts.join("\\") : "");
  }

  isBin() { return this.type === "BIN" || this.type === "ROOT"; }
  isSequence() { return !!this._sequence; }
  isOffline() { return this._offline; }
  hasVideo() { return this._hasVideo; }
  hasAudio() { return this._hasAudio; }

  _bump(what) {
    if (ProjectItem.observer) { ProjectItem.observer.bump(what); }
  }

  getMediaPath() { return this._mediaPath === null ? "" : this._mediaPath; }
  canChangeMediaPath() { return this.type === "CLIP"; }

  changeMediaPath(newPath, overrideChecks) {
    if (!overrideChecks && String(newPath).indexOf("mismatch") >= 0) {
      throw new Error("媒体一致性校验失败（可传 override_checks=true 跳过）");
    }
    this._mediaPath = String(newPath);
    this._bump("item.changeMediaPath");
    return 0;
  }

  createBin(name) {
    if (!this.isBin()) { return 0; }
    const bin = new ProjectItem(name, "BIN");
    bin._parent = this;
    this._children.push(bin);
    this._bump("item.createBin");
    return bin;
  }

  moveBin(newParent) {
    // 契约把 bin_move 定义为"把工程条目移动到目标素材箱"，所以这里接受任意
    // 非根条目（真机上 moveBin 对素材是否可用属"待真机验证"项；处理器对每个
    // 条目单独校验并在宿主拒绝时如实报 failed[]，不会假装成功）。
    if (this.type === "ROOT") { throw new Error("不能移动工程根"); }
    if (!newParent || !newParent.isBin()) { throw new Error("moveBin 的目标必须是素材箱"); }
    const oldParent = this._parent;
    if (oldParent) {
      const index = oldParent._children.indexOf(this);
      if (index >= 0) { oldParent._children.splice(index, 1); }
    }
    this._parent = newParent;
    newParent._children.push(this);
    this._bump("item.moveBin");
    return 0;
  }

  deleteBin() {
    const parent = this._parent;
    if (!parent) { return 0; }
    const index = parent._children.indexOf(this);
    if (index >= 0) { parent._children.splice(index, 1); }
    this._bump("item.deleteBin");
    return 0;
  }

  canProxy() { return this.type === "CLIP"; }
  hasProxy() { return !!this._proxyPath; }
  getProxyPath() { return this._proxyPath || 0; }

  attachProxy(mediaPath, isHiRes) {
    if (isHiRes) {
      this._mediaPath = String(mediaPath);
    } else {
      this._proxyPath = String(mediaPath);
    }
    this._bump("item.attachProxy");
    return 0;
  }

  getFootageInterpretation() {
    return { frameRate: this._frameRate, pixelAspectRatio: 1, fieldType: 0, alphaUsage: 0 };
  }

  getMarkers() { return this._markers; }

  findItemsMatchingMediaPath(matchPath, ignoreSubClips) {
    const root = this._root();
    const found = [];
    (function walk(node) {
      if (node._mediaPath && String(node._mediaPath).toLowerCase() === String(matchPath).toLowerCase()) {
        found.push(node);
      }
      node._children.forEach(walk);
    }(root));
    return found.length ? found : 0;
  }

  _root() {
    let node = this;
    while (node._parent) { node = node._parent; }
    return node;
  }
}

ProjectItem._counter = 0;
//: 由 createFakePremiere 注入，供 ProjectItem 的变更方法记录 mutation。
ProjectItem.observer = null;

class Project {
  constructor(name, filePath, fake) {
    this._fake = fake;
    this.name = name;
    this.path = filePath;
    this.documentID = "doc-" + Math.random().toString(16).slice(2);
    this.rootItem = new ProjectItem(name, "ROOT");
    this._sequences = [];
    this.activeSequence = 0;
  }

  get sequences() { return snapshotCollection(this._sequences, ["numSequences", "numItems"]); }

  save() { this._fake.bump("project.save"); return 0; }

  saveAs(newPath) {
    this.path = String(newPath);
    this.name = path.basename(String(newPath));
    this._fake.bump("project.saveAs");
    return 0;
  }

  closeDocument(saveFirst, promptIfDirty) {
    this._fake.bump("project.closeDocument");
    this._fake.state.project = null;
    return 0;
  }

  openSequence(sequenceId) {
    const found = this._sequences.filter(function (sequence) {
      return sequence.sequenceID === sequenceId;
    })[0];
    if (!found) { return false; }
    this.activeSequence = found;
    this._fake.bump("project.openSequence");
    return true;
  }

  newSequence(name, presetPath) {
    const sequence = new Sequence(name, this._fake);
    this._sequences.push(sequence);
    sequence.projectItem = new ProjectItem(name, "CLIP", { sequence: sequence });
    sequence.projectItem._parent = this.rootItem;
    this.rootItem._children.push(sequence.projectItem);
    this.activeSequence = sequence;
    this._fake.bump("project.newSequence");
    return sequence;
  }

  createNewSequence(name, sequenceId) {
    const sequence = new Sequence(name, this._fake);
    if (sequenceId) { sequence.sequenceID = sequenceId; }
    this._sequences.push(sequence);
    this.activeSequence = sequence;
    this._fake.bump("project.createNewSequence");
    return sequence;
  }

  createNewSequenceFromClips(name, projectItems, destinationBin) {
    const sequence = new Sequence(name, this._fake);
    this._sequences.push(sequence);
    sequence.projectItem = new ProjectItem(name, "CLIP", { sequence: sequence });
    sequence.projectItem._parent = destinationBin || this.rootItem;
    sequence.projectItem._parent._children.push(sequence.projectItem);
    this.activeSequence = sequence;
    let cursor = 0;
    projectItems.forEach(function (item) {
      sequence._videoTracks[0].insertClip(item, String(Math.round(cursor * TPS)));
      cursor += item._durationSeconds || 5;
    });
    this._fake.bump("project.createNewSequenceFromClips");
    return sequence;
  }

  importFiles(filePaths, suppressUI, targetBin, importAsNumberedStills) {
    const bin = targetBin || this.rootItem;
    filePaths.forEach(function (filePath) {
      const item = new ProjectItem(path.basename(String(filePath)), "CLIP", { mediaPath: String(filePath) });
      item._parent = bin;
      bin._children.push(item);
    });
    this._fake.bump("project.importFiles");
    return true;
  }

  deleteSequence(sequence) {
    const index = this._sequences.indexOf(sequence);
    if (index < 0) { return false; }
    this._sequences.splice(index, 1);
    if (this.activeSequence === sequence) { this.activeSequence = this._sequences[0] || 0; }
    this._fake.bump("project.deleteSequence");
    return true;
  }
}

/* ------------------------------------------------------------------ 伪 QE */

function wrapQeSequence(sequence, fake) {
  return {
    name: sequence.name,
    _public: sequence,
    getVideoTrackAt: function (index) {
      return sequence._videoTracks[index] ? wrapQeTrack(sequence._videoTracks[index], fake) : null;
    },
    getAudioTrackAt: function (index) {
      return sequence._audioTracks[index] ? wrapQeTrack(sequence._audioTracks[index], fake) : null;
    },
    // 接受 Time 对象 / tick 字符串 / 秒数（三种都容忍，模拟 QE 的不确定性）
    setWorkAreaInPoint: function (value) {
      sequence._workStart = coerceSeconds(value);
      fake.bump("qe.setWorkAreaInPoint");
      return 0;
    },
    setWorkAreaOutPoint: function (value) {
      sequence._workEnd = coerceSeconds(value);
      fake.bump("qe.setWorkAreaOutPoint");
      return 0;
    },
    getSelection: function () {
      return sequence.getSelection();
    }
  };
}

function wrapQeTrack(track, fake) {
  return {
    name: track.name,
    _public: track,
    getItemAt: function (index) {
      return track._clips[index] ? wrapQeItem(track._clips[index], track, fake) : null;
    },
    razor: function (value) {
      const seconds = coerceSeconds(value);
      const clip = track._clips.filter(function (candidate) {
        return candidate.start.seconds < seconds && candidate.end.seconds > seconds;
      })[0];
      if (!clip) { return 0; }
      const rightDuration = clip.end.seconds - seconds;
      const right = new TrackItem(clip.projectItem, seconds, rightDuration, track.mediaType, fake);
      right._track = track;
      right.inPoint = new Time(clip.inPoint.seconds + (seconds - clip.start.seconds));
      right.outPoint = new Time(clip.outPoint.seconds);
      clip.outPoint = new Time(clip.inPoint.seconds + (seconds - clip.start.seconds));
      clip.end = new Time(seconds);
      clip.duration = new Time(seconds - clip.start.seconds);
      track._clips.push(right);
      track._sort();
      fake.bump("qe.razor");
      fake.syncSequence(track);
      return 0;
    }
  };
}

function wrapQeItem(trackItem, track, fake) {
  return {
    name: trackItem.name,
    addVideoEffect: function (effectName) {
      trackItem.addComponent(new FakeComponent(String(effectName), "AE." + effectName, [
        new FakeComponentParam("Amount", "Amount", 50, false)
      ]));
      fake.bump("qe.addVideoEffect");
      return 0;
    },
    addTransition: function (transitionName, position) {
      track._transitions.push({ name: String(transitionName), position: position });
      fake.bump("qe.addTransition");
      return 0;
    },
    moveClipToTime: function (value) {
      const target = coerceSeconds(value);
      const delta = target - trackItem.start.seconds;
      trackItem.start = new Time(target);
      trackItem.end = new Time(trackItem.end.seconds + delta);
      fake.bump("qe.moveClipToTime");
      return 0;
    },
    setStartTime: function (value) {
      const target = coerceSeconds(value);
      const delta = target - trackItem.start.seconds;
      trackItem.start = new Time(target);
      trackItem.end = new Time(trackItem.end.seconds + delta);
      fake.bump("qe.setStartTime");
      return 0;
    },
    setEndTime: function (value) {
      trackItem.end = new Time(coerceSeconds(value));
      fake.bump("qe.setEndTime");
      return 0;
    }
  };
}

/* -------------------------------------------------------------- 创建伪宿主 */

function createFakePremiere(options) {
  const opts = options || {};
  const repoJsxDir = opts.jsxDir || path.resolve(__dirname, "..", "..", "..", "cep-extension", "jsx");
  const state = {
    project: null,
    mutations: 0,
    mutationLog: [],
    jobs: 0,
    qeEnabled: false,
    projectDir: opts.projectDir || os.tmpdir()
  };

  const fake = {
    state: state,

    bump(what) {
      state.mutations += 1;
      state.mutationLog.push(what);
    },

    resetMutations() {
      state.mutations = 0;
      state.mutationLog = [];
    },

    /** 同步序列时长（真实宿主在编辑后也会更新 end）。 */
    syncSequence(track) {
      if (track && track._sequence) { track._sequence._recalcEnd(); }
    },

    /** 稳定快照：dry-run 前后对比用。 */
    snapshot() {
      const project = state.project;
      const snapshot = { hasProject: !!project, mutations: state.mutations, jobs: state.jobs };
      if (!project) { return JSON.stringify(snapshot); }
      const tree = [];
      (function walk(item) {
        tree.push({ path: item.treePath, type: item.type, name: item.name, mediaPath: item._mediaPath });
        item._children.forEach(walk);
      }(project.rootItem));
      const sequences = project._sequences.map(function (sequence) {
        return {
          name: sequence.name,
          inPoint: sequence._inPoint,
          outPoint: sequence._outPoint,
          workArea: [sequence._workStart, sequence._workEnd],
          playhead: sequence._playheadTicks,
          end: sequence.end,
          tracks: sequence._allTracks().map(function (track) {
            return {
              name: track.name,
              transitions: track._transitions.length,
              clips: track._clips.map(function (clip) {
                return {
                  name: clip.name,
                  start: clip.start.ticks,
                  end: clip.end.ticks,
                  inPoint: clip.inPoint.ticks,
                  outPoint: clip.outPoint.ticks,
                  components: clip._components.length
                };
              })
            };
          }),
          markers: sequence.markers._markers.map(function (marker) {
            return {
              name: marker.name,
              comments: marker.comments,
              type: marker.type,
              start: marker.start.ticks,
              end: marker.end.ticks
            };
          })
        };
      });
      snapshot.tree = tree;
      snapshot.sequences = sequences;
      snapshot.projectPath = project.path;
      return JSON.stringify(snapshot);
    },

    /** 建一个标准夹具工程：Bin A（两个素材）+ Seq 1（V1 上有一个片段）。 */
    seed() {
      const project = new Project("demo.prproj", path.join(state.projectDir, "demo.prproj"), fake);
      state.project = project;
      const mediaDir = path.join(state.projectDir, "media");
      const binA = new ProjectItem("Bin A", "BIN");
      binA._parent = project.rootItem;
      project.rootItem._children.push(binA);
      const clip1 = new ProjectItem("clip1.mp4", "CLIP", {
        mediaPath: path.join(mediaDir, "clip1.mp4"), durationSeconds: 5
      });
      clip1._parent = binA;
      binA._children.push(clip1);
      const clip2 = new ProjectItem("clip2.mp4", "CLIP", {
        mediaPath: path.join(mediaDir, "clip2.mp4"), durationSeconds: 8
      });
      clip2._parent = binA;
      binA._children.push(clip2);
      const sequence = project.newSequence("Seq 1");
      project.activeSequence = sequence;
      sequence._videoTracks[0].insertClip(clip1, "0");
      fake.resetMutations();
      return { project, binA, clip1, clip2, sequence };
    },

    /** 取夹具里的片段对象。 */
    clipAt(trackType, trackIndex, clipIndex) {
      const sequence = state.project.activeSequence;
      const tracks = trackType === "audio" ? sequence._audioTracks : sequence._videoTracks;
      const track = tracks[trackIndex - 1];
      if (!track) { return null; }
      return track._clips[clipIndex] || null;
    },

    globals: null
  };

  const app = {
    version: "25.0.0",
    build: "70",
    get project() { return state.project; },
    properties: {
      getProperty(key) { return key === "BE.Prefs.Language" ? "zh_CN" : undefined; }
    },
    encoder: {
      encodeSequence(sequence, outputPath, presetPath, workArea, remove) {
        state.jobs += 1;
        fake.bump("encoder.encodeSequence");
        return "job-" + state.jobs;
      },
      encodeProjectItem(item, outputPath, presetPath, workArea, remove) {
        state.jobs += 1;
        fake.bump("encoder.encodeProjectItem");
        return "job-" + state.jobs;
      },
      encodeFile(filePath, outputPath, presetPath, workArea, remove, inPoint, outPoint) {
        state.jobs += 1;
        fake.bump("encoder.encodeFile");
        return "job-" + state.jobs;
      },
      startBatch() { fake.bump("encoder.startBatch"); return 0; }
    },
    newProject(filePath) {
      state.project = new Project(path.basename(String(filePath)), String(filePath), fake);
      fake.bump("app.newProject");
      return true;
    },
    openDocument(filePath) {
      state.project = new Project(path.basename(String(filePath)), String(filePath), fake);
      fake.bump("app.openDocument");
      return true;
    },
    isDocument(filePath) { return /\.prproj$/i.test(String(filePath)); },
    isDocumentOpen() { return state.project !== null; },
    enableQE() { state.qeEnabled = true; return true; },
    setSDKEventMessage() { return true; },
    getCurrentProjectViewSelection() { return null; }
  };

  const dollar = {
    fileName: path.join(repoJsxDir, "ops", "timeline.jsx"),
    line: 30,
    locale: "zh_CN",
    global: null,
    writeln() {},
    sleep() { return true; }
  };

  const qe = {
    project: {
      getActiveSequence() {
        const sequence = state.project ? state.project.activeSequence : 0;
        if (!sequence || typeof sequence === "number") { return null; }
        return wrapQeSequence(sequence, fake);
      },
      getSequenceAt(index) {
        const sequence = state.project ? state.project._sequences[index] : null;
        return sequence ? wrapQeSequence(sequence, fake) : null;
      },
      getVideoEffectList() { return ["Gaussian Blur", "Opacity", "Crop"]; },
      getAudioEffectList() { return ["Volume", "Balance"]; }
    }
  };

  fake.globals = {
    app: app,
    $: dollar,
    Time: Time,
    File: File,
    Folder: Folder,
    qe: qe,
    TPS: TPS
  };
  fake.app = app;
  ProjectItem.observer = fake;

  return fake;
}

module.exports = {
  createFakePremiere: createFakePremiere,
  FakeComponent: FakeComponent,
  FakeComponentParam: FakeComponentParam,
  TPS: TPS
};
