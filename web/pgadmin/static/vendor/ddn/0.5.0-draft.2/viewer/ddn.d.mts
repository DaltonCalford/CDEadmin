/** DDN 0.5.0-draft.2 public API. Source access is not a security boundary. */
export type SourceFiles = Record<string, string>;
export type Placement = 'source'|'auto'|'grid'|'manual'|'fit_grid'|'circular'|'radial'|'layered'|'tree'|'spanning_tree'|'mindmap'|'grouped'|'organic';
export type ProjectionKind = 'graph'|'chen'|'matrix'|'table'|'panels'|'chart'|'timeline'|'fishbone'|'decision';
export interface ProjectionMark {id:string;sourceIds:string[];property?:string;[key:string]:unknown}
export interface Options {
  endpointOrdering?: 'source'|'optimize'|'preserve';
  mark?:'source'|'bar'|'line'|'area'|'point'|'pie'|'donut';
  placement?: Placement; autoPlace?: boolean|null; center?: 'source'|'pins'|'content'; gridStep?: number|null;
  theme?: 'source'|'default'|'base'|'neutral'|'forest'|'dark'|'night'; look?: 'classic'|'handDrawn'|'neo'|null;
  routing?: 'source'|'orthogonal'|'straight'|'curved'|'rounded'; crossings?: 'source'|'gap'|'bridge'|'square_bridge';
  fields?: 'source'|'names'|'none'; domains?: 'source'|'show'|'hide'; datatypes?: 'source'|'show'|'hide'; depth?: number|null;
  labels?: 'source'|'numbers'|'text'|'tokens'; kind?: 'source'|'icon_token'|'icon'|'text'|'none';
  page?: 'source'|'content'|'web'|'a4-landscape'|'a4-portrait'|'letter-landscape'|'letter-portrait'|'custom';
  width?: number; height?: number; font?: 'source'|'sans'|'serif'|'mono'|'handwriting'; fontSize?: number|null;
  roughness?: number|null; hachure?: boolean|null;
}
export interface LayoutState { format:'ddn-layout-state@1'; view:string; positions:Record<string,[number,number]> }
export interface Diagnostic {code:string;severity?:'info'|'warning'|'error';message:string;source?:string;offset?:number}
export interface Scene {marks?:ProjectionMark[];projection?:{kind:ProjectionKind;profile:string;sourceIds?:string[];quantitative?:boolean;[key:string]:unknown};nodes:Array<{id:string;x:number;y:number;w:number;h:number;[key:string]:unknown}>; routes:Array<Record<string,unknown>>;layoutState?:LayoutState;[key:string]:unknown}
export interface RenderRequest {entry:string;view:string;overrides?:Options;layoutState?:LayoutState|null}
export interface RenderResult {svg:string;scene:Scene;layoutState:LayoutState|null;diagnostics:Diagnostic[];entry:string;view:string;revision:number;milliseconds:number;modelFingerprint:string;sourceMap:Record<string,Record<string,unknown>>;dependencies:string[];profiles:Record<string,unknown>;capabilities:Record<string,unknown>;keys:Record<string,number>;overrides:Options}
export interface Snapshot extends RenderRequest {format:'ddn-workspace@1';runtime:Record<string,string>;files:SourceFiles}
export interface TextEdit {file:string;start:number;end:number;text:string}
export interface Workspace {
 readonly revision:number;getFiles():SourceFiles;entries():Array<{file:string;views:Array<{id:string;name:string}>}>;views(entry:string):Array<{id:string;name:string}>;
 analyze(file:string):Record<string,unknown>;resolve(entry:string,view:string):Record<string,unknown>;inspect(entry:string,view:string):Record<string,unknown>;
 updateFiles(changes:SourceFiles):number;replaceFiles(files:SourceFiles):number;removeFile(file:string,options?:{force?:boolean}):number;dependents(file:string):string[];renameFile(oldName:string,newName:string):number;
 applyEdits(edits:TextEdit[],options?:{expectedRevision?:number;entry?:string;view?:string}):number;history():{canUndo:boolean;canRedo:boolean;undoLabel:string;redoLabel:string};undo():boolean;redo():boolean;
 subscribe(fn:(event:{revision:number;changedFiles:string[]})=>void):()=>void;
 renderSync(request:RenderRequest):RenderResult;render(request:RenderRequest):Promise<RenderResult>;exportModel(request:RenderRequest):string;exportVegaLite(request:RenderRequest):Record<string,unknown>;projectionPlan(entry:string,view:string):Record<string,unknown>; evaluateDecision(entry:string,view:string,input:Record<string,unknown>):Record<string,unknown>; simulateLifecycle(entry:string,view:string,events:Array<{event:string;data?:Record<string,unknown>}>,expected?:string):Record<string,unknown>;
 snapshot(entry:string,view:string,overrides?:Options,layoutState?:LayoutState|null):Snapshot;destroy():void;
}
export interface MountOptions extends RenderRequest {workspace:Workspace}
export interface DiagramElement extends HTMLElement {
 ready:Promise<RenderResult|{superseded:true}>;setOptions(options:Options):Promise<RenderResult|{superseded:true}>;
 exportSVG():string;getState():Record<string,unknown>;destroy():void;
}
export interface MatrixEdit {row:string;column:string;value?:unknown;remove?:boolean;id?:string}
export interface Authoring {
 setMatrixCell(ws:Workspace,entry:string,view:string,row:string,column:string,value:unknown,options?:{remove?:boolean;id?:string}):number;
 setMatrixCells(ws:Workspace,entry:string,view:string,changes:MatrixEdit[]):number;
 setRecordValue(ws:Workspace,entry:string,view:string,id:string,key:string,value:unknown):number;
 setAssignment(ws:Workspace,entry:string,view:string,id:string,code:string):number;
 value(value:unknown):string;
 setLabel(ws:Workspace,entry:string,view:string,id:string,label:string):number;
 setProperty(ws:Workspace,entry:string,view:string,id:string,key:string,value:unknown):number;
 pin(ws:Workspace,entry:string,view:string,id:string,x:number,y:number):number;unpin(ws:Workspace,entry:string,view:string,id:string):number|false;hide(ws:Workspace,entry:string,view:string,id:string):number|false;
 addElement(ws:Workspace,entry:string,view:string,data:{id:string;name?:string;kind?:string}):number;
 addField(ws:Workspace,entry:string,view:string,parent:string,data:{id:string;name?:string}):number;
 addRelation(ws:Workspace,entry:string,view:string,data:{id:string;name?:string;kind?:string;from:string;to:string}):number;
 deleteDefinition(ws:Workspace,entry:string,view:string,id:string):number;
 sourceOf(ws:Workspace,entry:string,view:string,id:string):{file:string;start:number;end:number;type:string;id:string;name:string;properties:Record<string,unknown>};
}
export interface IO {
 open(files:FileList|File[],options?:{directory?:boolean}):Promise<{files:SourceFiles;snapshot:Snapshot;ignored:string[]}>;
 toJSON(snapshot:Snapshot):string;toZIP(snapshot:Snapshot):Uint8Array;unzip(bytes:Uint8Array):Promise<{files:Record<string,string>;ignored:string[]}>;zipStore(files:Record<string,string>):Uint8Array;
 crc32(bytes:Uint8Array):number;download(name:string,data:BlobPart,type?:string):void;
}
export const profileCatalogue:{version:string;profiles:Array<Record<string,unknown>>;kinds:Array<Record<string,unknown>>;relationships:Array<Record<string,unknown>>;[key:string]:unknown};
export const VERSION:string;
export const runtime:Record<string,string>;
export function createWorkspace(files:SourceFiles):Workspace;
export function registerWorkspace(name:string,files:SourceFiles|Workspace):Workspace;
export function mount(element:Element,options:MountOptions):DiagramElement;
export function fromSnapshot(snapshot:Snapshot):MountOptions;
export function parse(source:string,file?:string):Record<string,unknown>;
export const authoring:Authoring;
export const io:IO;
declare const DDNLive:{profileCatalogue:typeof profileCatalogue;VERSION:typeof VERSION;runtime:typeof runtime;createWorkspace:typeof createWorkspace;registerWorkspace:typeof registerWorkspace;mount:typeof mount;fromSnapshot:typeof fromSnapshot;authoring:Authoring;io:IO;parse:typeof parse;setTextProvider(fn:((text:string,size:number,font:string,weight:number)=>{width:number;ascent?:number;descent?:number})|null,name?:string):void;setTextMetrics(metrics:Record<string,unknown>):void};
export default DDNLive;
