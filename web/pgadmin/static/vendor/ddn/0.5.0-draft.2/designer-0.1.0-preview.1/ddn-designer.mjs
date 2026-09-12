import './ddn.global.js';
import * as module from './ddn-designer.js';
const D=globalThis.DDNDesigner||module.default;
export const VERSION=D.VERSION,RUNTIME_VERSION=D.RUNTIME_VERSION,createWorkspace=D.createWorkspace,io=D.io,createSession=D.createSession,mount=D.mount,blankFiles=D.blankFiles,catalogue=D.catalogue;
export default D;
