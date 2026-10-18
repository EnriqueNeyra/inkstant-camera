// Every message the gallery shows. One place, ready for translation later.
export const S = {
  photos: (n) => `${fmtInt(n)} photo${n === 1 ? "" : "s"}`,
  today: "Today",
  yesterday: "Yesterday",
  unknownDate: "Unknown date",
  cameraWifi: "Camera Wi-Fi",
  saver: "Saver",
  selectPhotos: "Select photos",
  selected: (n) => `${fmtInt(n)} selected`,
  select: "Select",
  deselect: "Deselect",
  newPhotos: (n) => `${fmtInt(n)} new photo${n === 1 ? "" : "s"}`,
  // Banners
  storageFull: "Storage full. The camera can't take photos until you delete some.",
  storageLow: (n) => `Storage low: about ${fmtInt(n)} photos left.`,
  batteryLow: (pct) => `Battery low (${pct}%). Charge the camera soon.`,
  clockUnset: "The camera's clock isn't set.",
  cameraError: (d) => `${d}. Try turning the camera off and on again.`,
  displayError: "The display module isn't responding. Take the print off and put it back on.",
  setupFailed: (item) => `Setup check found a problem: ${item}.`,
  updating: "Updating the camera…",
  updateFailed: (why, v) => `The update didn't finish: ${why}. The camera is still on version ${v}.`,
  oneMore: (n) => `${n} more`,
  selectPhotosAction: "Select photos",
  usePhoneTime: "Use this phone's time",
  seeDetails: "See details",
  // Toasts
  photoDeleted: "Photo deleted",
  printNotReady: "This photo's print isn't ready yet. Try again in a minute.",
  photosDeleted: (n) => `${fmtInt(n)} photos deleted`,
  developing: "Developing on the camera, about 20 seconds",
  queued: "Queued: the camera will show it after the current photo",
  downloadStarted: "Download started. Keep the camera on until it finishes.",
  saved: "Saved",
  couldntSave: (r) => `Couldn't save: ${r}`,
  timeSet: "Time set",
  networkAdded: "Network added",
  networkForgotten: "Network forgotten",
  sharingStopped: "Sharing stopped",
  // Download sheet
  downloadN: (n) => `Download ${fmtInt(n)} photo${n === 1 ? "" : "s"}`,
  downloadMeta: (bytes, secs) => `${fmtBytes(bytes)} · about ${fmtDuration(secs)}`,
  howtoIos: "Safari saves one ZIP file to Files → Downloads. Open it there, then select the photos and tap Share → Save Images.",
  howtoAndroid: "Chrome saves one ZIP file to Downloads. Open it in the Files app to extract the photos.",
  howtoComputer: "Your browser saves one ZIP file.",
  // Viewer
  photoOf: (i, n) => `Photo ${fmtInt(i)} of ${fmtInt(n)}`,
  noModule: "No display module in the camera",
  cameraBusy: "The camera is busy",
  deleteN: (n) => (n === 1 ? "Delete this photo?" : `Delete ${fmtInt(n)} photos?`),
  // Settings
  ready: "Ready",
  asleep: "Asleep",
  asleepSub: "Tap the shutter button to wake it",
  starting: "Starting",
  problem: "Problem",
  moduleIn: "Module in",
  developingShort: "Developing…",
  noModuleShort: "No module",
  stopsAfter: (m) => `Turns off after ${m} min without use`,
  staysOn: "Stays on while charging",
  notSharing: "Off",
  saverOn: "On",
  saverAuto: "On because the battery is low",
  saverOff: "Off",
  turnOnAt: (p) => `Turn on at ${p}%`,
  never: "Never",
  min: (m) => `${m} min`,
  hours: (h) => (h === 1 ? "1 hour" : `${h} hours`),
  pct: (p) => `${p}% battery`,
  freeAbout: (free, n) => `${fmtBytes(free)} free · about ${fmtInt(n)} more photos`,
  usedLine: (n, used) => `${S.photos(n)} · ${fmtBytes(used)}`,
  behind: (m) => `The camera's clock is ${m} behind this phone`,
  ahead: (m) => `The camera's clock is ${m} ahead of this phone`,
  needsInternet: "Needs internet. Share at home to update.",
  upToDate: "This is the latest version.",
  available: (v) => `Version ${v} is available`,
  forgetText: (n) => `Forget “${n}”? The camera won't join it for sharing any more.`,
  forget: "Forget",
  pass: "Passed",
  fail: "Failed",
  notRun: "The setup check hasn't run yet.",
  needName: "Enter a network name.",
  needPassword: "Passwords are 8 to 63 characters.",
};

export function fmtInt(n) {
  return new Intl.NumberFormat().format(n);
}

export function fmtBytes(b) {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`;
  return `${Math.max(1, Math.round(b / 1e3))} KB`;
}

export function fmtDuration(secs) {
  if (secs < 60) return `${Math.max(10, Math.ceil(secs / 10) * 10)} seconds`;
  const m = Math.ceil(secs / 60);
  return m === 1 ? "1 minute" : `${m} minutes`;
}
