/** DDN Designer 0.1.0-preview.1. Implementation declarations, not future API proposals. */
export as namespace DDNDesigner;
export type Value = null | boolean | number | string | Value[] | { [key: string]: Value };
export type Files = Record<string, string>;
export type Reference = { $ref: string };
export interface Diagnostic { code: string; message: string; severity?: string; source?: string; offset?: number; }
export interface PresentationOptions {
  look?: 'classic'|'handDrawn'|'neo'|null;
  theme?: 'source'|'default'|'base'|'neutral'|'dark'|'night'|'forest';
  placement?: 'source'|'auto'|'grid'|'manual'|'fit_grid'|'circular'|'radial'|'layered'|'tree'|'spanning_tree'|'mindmap'|'grouped'|'organic';
  routing?: 'source'|'orthogonal'|'straight'|'curved'|'rounded';
  crossings?: 'source'|'gap'|'bridge'|'square_bridge';
  autoPlace?: boolean|null; center?: 'source'|'pins'|'content'; gridStep?: number|null;
  endpointOrdering?: 'source'|'optimize'|'preserve';
  fields?: 'source'|'names'|'none'; domains?: 'source'|'show'|'hide'; datatypes?: 'source'|'show'|'hide'; depth?: number|null;
  labels?: 'source'|'numbers'|'text'|'tokens'; kind?: 'source'|'icon_token'|'icon'|'text'|'none';
  mark?: 'source'|'bar'|'line'|'area'|'point'|'pie'|'donut';
  page?: 'source'|'content'|'web'|'a4-landscape'|'a4-portrait'|'letter-landscape'|'letter-portrait'|'custom';
  width?: number; height?: number; font?: 'source'|'sans'|'serif'|'mono'|'handwriting'; fontSize?: number|null;
  roughness?: number|null; hachure?: boolean|null;
}
export interface LayoutState { format: string; view: string; positions: Record<string,[number,number]>; [key:string]: unknown; }
export interface WorkspaceSnapshot { format:'ddn-workspace@1'|'ddn-live-snapshot@0.1'; files:Files; entry:string; view:string; overrides?:PresentationOptions; layoutState?:LayoutState|null; runtime?:unknown; }
export interface Definition { id:string; local:string; path:string; type:string; name:string; kind:string; properties:Record<string,Value>; file:string; start:number; end:number; parent:string|null; }
export interface ViewInfo { id:string; uid:string; file:string; name:string; }
export interface Inspection { definitions:Definition[]; views:ViewInfo[]; view:{id:string;name:string;properties:Record<string,Value>;groups:Record<string,Record<string,Value>>};writeTargets:{id:string;name:string;file:string}[];revision:number; }
export interface SceneNode {id:string;x:number;y:number;w:number;h:number;pinned:boolean;fieldRows?:{id:string;top:number;h:number}[];[key:string]:unknown;}
export interface RenderResult {svg:string;status:'valid'|'incomplete';diagnostics:Diagnostic[];scene:{nodes?:SceneNode[];[key:string]:unknown};sourceMap?:Record<string,unknown>;authoredProfile?:string;constructionProjection?:string;[key:string]:unknown;}
export interface SessionEvent {type:string;revision?:number;[key:string]:unknown;}
export interface Review {revision:number;scope:'workspace'|'current';views:{entry:string;view:string;status:'valid'|'error';diagnostics:Diagnostic[]}[];}
export interface ChangePlan {readonly revision:number;readonly entry:string;readonly view:string;readonly label:string;readonly selection:unknown;readonly files:{file:string;before:string;after:string}[];readonly affectedViews:{entry:string;view:string;name:string}[];readonly status:'valid'|'incomplete';readonly diagnostics:Diagnostic[];readonly needsConfirmation:boolean;}
export interface CommandResult {revision:number;selection:unknown;status:'valid'|'incomplete';}
export interface FieldInput {id:string;name?:string;properties?:Record<string,Value>;}
export interface MatrixChange {row:string;column:string;value?:Value;remove?:boolean;}
export type Command =
 | {type:'create';id?:string;name?:string;kind?:string;writeTo?:string;properties?:Record<string,Value>;fields?:FieldInput[];at?:[number,number]}
 | {type:'connect';id?:string;name?:string;from:string;to:string;kind?:string;writeTo?:string;properties?:Record<string,Value>}
 | {type:'label';id:string;value:string}
 | {type:'property';id:string;key:string;value?:Value;unset?:boolean}
 | {type:'properties';id:string;values:Record<string,Value>;unset?:string[]}
 | {type:'kind';id:string;value:string}
 | {type:'viewProperties';values:Record<string,Value>}
 | {type:'concern';concern:'projection'|'layout'|'style'|'display'|'legend'|'publication'|'validation'|'export';values:Record<string,Value>}
 | {type:'addField';parent:string;id?:string;name?:string;properties?:Record<string,Value>}
 | {type:'reorderField';id:string;direction:'up'|'down'}
 | {type:'reconnect';id:string;from?:string;to?:string}
 | {type:'route';id:string;values:Record<string,Value>}
 | {type:'resetRoute'|'unpin'|'show'|'hide';id:string}
 | {type:'pin';id:string;at:[number,number]}
 | {type:'resize';id:string;size:[number,number]}
 | {type:'delete';id:string;cascade?:boolean}
 | {type:'duplicate';id:string;newId?:string;name?:string;writeTo?:string;at?:[number,number]}
 | {type:'newView';id:string;name?:string;data?:Reference[];projection?:Record<string,Value>}
 | {type:'cloneView';newId:string;name?:string}
 | {type:'frame';id:string;scope:string;members:string[];dimension?:string}
 | {type:'matrix';changes:MatrixChange[]}
 | {type:'batch';commands:Command[]};
export interface Workspace {
 readonly revision:number; getFiles():Files; updateFiles(changes:Files):unknown; replaceFiles(files:Files):unknown;
 entries():{file:string;views:{id:string;name?:string}[]}[];views(entry:string):{id:string;name?:string}[];
 subscribe(listener:(event:Record<string,unknown>)=>void):()=>void; undo():unknown;redo():unknown;history():{canUndo:boolean;canRedo:boolean};
 renameFile(from:string,to:string):unknown;removeFile(path:string):unknown;
 renderSync(options:{entry:string;view:string;overrides?:PresentationOptions;layoutState?:LayoutState}):Omit<RenderResult,'status'>;
 snapshot(entry:string,view:string,overrides?:PresentationOptions,layoutState?:LayoutState|null):WorkspaceSnapshot;
 destroy():void;
}
export interface SessionOptions {
 files?:Files; workspace?:Workspace; entry?:string;view?:string;validation?:'draft'|'strict';readOnly?:boolean;overrides?:PresentationOptions;
 beforeCommit?:(plan:ChangePlan)=>boolean|void;
}
export interface Session {
 readonly workspace:Workspace;readonly entry:string;readonly view:string;readonly revision:number;readonly readOnly:boolean;readonly validation:'draft'|'strict';readonly result:RenderResult|null;readonly ir:unknown;
 inspect():Inspection;sourceOf(id:string):{file:string;start:number;end:number;text:string};getFiles():Files;
 prepare(command:Command):ChangePlan;commit(plan:ChangePlan):CommandResult;execute(command:Command):CommandResult;
 updateSource(file:string,text:string):void;replaceFiles(files:Files):void;renameFile(from:string,to:string):void;removeFile(path:string):void;
 undo():unknown;redo():unknown;history():{canUndo:boolean;canRedo:boolean};
 setReadOnly(flag:boolean):void;setValidation(mode:'draft'|'strict'):void;setView(entry:string,view:string):void;
 setOptions(options:PresentationOptions):void;getOptions():PresentationOptions;resetOptions():void;
 render():RenderResult;review(all?:boolean):Review;exportSVG():string;snapshot():WorkspaceSnapshot;load(snapshot:WorkspaceSnapshot):void;
 evaluateDecision(input:Record<string,unknown>):unknown;simulateLifecycle(events:unknown[],expected?:unknown):unknown;
 subscribe(listener:(event:SessionEvent)=>void):()=>void;destroy():void;
}
export interface MountOptions extends SessionOptions {session?:Session;uiTheme?:'night'|'light';snap?:number;nonce?:string;onChange?:(snapshot:WorkspaceSnapshot,event:Record<string,unknown>)=>void;}
export interface DesignerController extends EventTarget {
 readonly session:Session;readonly ready:Promise<DesignerController>;readonly result:RenderResult|null;
 setView(entry:string,view:string):Promise<DesignerController>;
 setOptions(options:PresentationOptions):Promise<DesignerController>;
 setReadOnly(flag:boolean):Promise<DesignerController>;setValidation(mode:'draft'|'strict'):Promise<DesignerController>;
 execute(command:Command):CommandResult;getSnapshot():WorkspaceSnapshot;load(snapshot:WorkspaceSnapshot):Promise<DesignerController>;
 importFiles(files:FileList|File[],options?:{directory?:boolean}):Promise<DesignerController>;
 exportSVG():string;select(id:string,append?:boolean):void;destroy():void;
}
export const VERSION:string, RUNTIME_VERSION:string;
export function blankFiles(template?:'data'|'flow'|'dfd'):Files;
export function createWorkspace(files:Files):Workspace;
export function createSession(options?:SessionOptions):Session;
export function mount(container:HTMLElement,options?:MountOptions):DesignerController;
export const catalogue:{kinds:{id:string;label:string;group:string;template:string;recipe:unknown}[];relations:{id:string;label:string}[];profiles:unknown[];choices:Record<string,unknown>};
export const io:{
 open(files:FileList|File[],options?:{directory?:boolean}):Promise<{snapshot:WorkspaceSnapshot;files:Files;ignored:string[]}>;
 toJSON(snapshot:WorkspaceSnapshot):string;
 toZIP(snapshot:WorkspaceSnapshot):Uint8Array;
 download(name:string,content:string|Uint8Array,mime?:string):void;
};
declare const API:{VERSION:typeof VERSION;RUNTIME_VERSION:typeof RUNTIME_VERSION;blankFiles:typeof blankFiles;createSession:typeof createSession;createWorkspace:typeof createWorkspace;mount:typeof mount;catalogue:typeof catalogue;io:typeof io;};
export default API;
