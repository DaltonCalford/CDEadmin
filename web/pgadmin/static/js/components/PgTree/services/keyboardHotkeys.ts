import { Notificar } from 'notificar';
import { FileEntry, Directory, FileType } from 'react-aspen';
import { FileTreeXEvent, IFileTreeXHandle } from '../types';

export class KeyboardHotkeys {
  private readonly hotkeyActions = {
    'ArrowUp': () => this.jumpToPrevItem(),
    'ArrowDown': () => this.jumpToNextItem(),
    'ArrowRight': () => this.expandOrJumpToFirstChild(),
    'ArrowLeft': () => this.collapseOrJumpToFirstParent(),
    'Space': () => this.toggleDirectoryExpand(),
    'Enter': () => this.selectFileOrToggleDirState(),
    'Home': () => this.jumpToFirstItem(),
    'End': () => this.jumpToLastItem(),
    'Escape': () => this.resetSteppedOrSelectedItem(),
    'Ctrl+KeyC': () => this.copyEntry(),
  };

  constructor(private readonly fileTreeX: IFileTreeXHandle, private readonly events: Notificar<FileTreeXEvent>) { }

  public handleKeyDown = (ev: React.KeyboardEvent) => {
    if (!this.fileTreeX.hasDirectFocus()) {
      return false;
    }
    let { code } = ev.nativeEvent;

    if((ev.nativeEvent.ctrlKey || ev.nativeEvent.metaKey) && ev.nativeEvent.key !== 'Control') {
      code = `Ctrl+${code}`;
    }
    if (code in this.hotkeyActions) {
      ev.preventDefault();
      this.hotkeyActions[code]();
      return true;
    }
  };

  private readonly jumpToFirstItem = (): void => {
    const { root } = this.fileTreeX.getModel();
    for(let index = 0; index < root.branchSize; index += 1) {
      const entry = root.getFileEntryAtIndex(index);
      if(this.isSelectable(entry)) {
        this.fileTreeX.setActiveFile(entry, true);
        return;
      }
    }
  };

  private readonly jumpToLastItem = (): void => {
    const { root } = this.fileTreeX.getModel();
    for(let index = root.branchSize - 1; index >= 0; index -= 1) {
      const entry = root.getFileEntryAtIndex(index);
      if(this.isSelectable(entry)) {
        this.fileTreeX.setActiveFile(entry, true);
        return;
      }
    }
  };

  private readonly jumpToNextItem = (): void => {
    const { root } = this.fileTreeX.getModel();
    let currentPseudoActive = this.fileTreeX.getActiveFile();
    if (!currentPseudoActive) {
      const selectedFile = this.fileTreeX.getActiveFile();
      if (selectedFile) {
        currentPseudoActive = selectedFile;
      } else {
        return this.jumpToFirstItem();
      }
    }
    const idx = root.getIndexAtFileEntry(currentPseudoActive);
    if (idx > -1) {
      for(let index = idx + 1; index < root.branchSize; index += 1) {
        const entry = root.getFileEntryAtIndex(index);
        if(this.isSelectable(entry)) {
          this.fileTreeX.setActiveFile(entry, true);
          return;
        }
      }
      return this.jumpToFirstItem();
    }
  };

  private readonly jumpToPrevItem = (): void => {
    const { root } = this.fileTreeX.getModel();
    let currentPseudoActive = this.fileTreeX.getActiveFile();
    if (!currentPseudoActive) {
      const selectedFile = this.fileTreeX.getActiveFile();
      if (selectedFile) {
        currentPseudoActive = selectedFile;
      } else {
        return this.jumpToLastItem();
      }
    }
    const idx = root.getIndexAtFileEntry(currentPseudoActive);
    if (idx > -1) {
      for(let index = idx - 1; index >= 0; index -= 1) {
        const entry = root.getFileEntryAtIndex(index);
        if(this.isSelectable(entry)) {
          this.fileTreeX.setActiveFile(entry, true);
          return;
        }
      }
      return this.jumpToLastItem();
    }
  };

  private readonly isSelectable = (item: FileEntry | Directory): boolean =>
    Boolean(item) && item._metadata?.data?.disabled !== true;

  private expandOrJumpToFirstChild(): void {
    const currentPseudoActive = this.fileTreeX.getActiveFile();
    if (currentPseudoActive && currentPseudoActive.type === FileType.Directory) {
      if ((currentPseudoActive as Directory).expanded) {
        return this.jumpToNextItem();
      } else {
        this.fileTreeX.openDirectory(currentPseudoActive as Directory);
      }
    }
  }

  private collapseOrJumpToFirstParent(): void {
    const currentPseudoActive = this.fileTreeX.getActiveFile();
    if (currentPseudoActive) {
      if (currentPseudoActive.type === FileType.Directory && (currentPseudoActive as Directory).expanded) {
        return this.fileTreeX.closeDirectory(currentPseudoActive as Directory);
      }
      this.fileTreeX.setActiveFile(currentPseudoActive.parent, true);
    }
  }

  private readonly selectFileOrToggleDirState = (): void => {
    const currentPseudoActive = this.fileTreeX.getActiveFile();
    if (!currentPseudoActive) { return; }
    if (currentPseudoActive._metadata?.data?._type === 'cde_resource') {
      this.events.dispatch(FileTreeXEvent.onTreeEvents, null, 'activated', currentPseudoActive);
      return;
    }
    if (currentPseudoActive.type === FileType.Directory) {
      this.fileTreeX.toggleDirectory(currentPseudoActive as Directory);
    } else if (currentPseudoActive.type === FileType.File) {
      this.fileTreeX.setActiveFile(currentPseudoActive as FileEntry, true);
    }
  };

  private readonly toggleDirectoryExpand = (): void => {
    const currentPseudoActive = this.fileTreeX.getActiveFile();
    if (!currentPseudoActive) { return; }
    if(this.fileTreeX.toggleNodeCheck(currentPseudoActive)) {
      return;
    }
    if (currentPseudoActive.type === FileType.Directory) {
      this.fileTreeX.toggleDirectory(currentPseudoActive as Directory);
    }
  };

  private readonly resetSteppedOrSelectedItem = (): void => {
    const currentPseudoActive = this.fileTreeX.getActiveFile();
    if (currentPseudoActive) {
      return this.resetSteppedItem();
    }
    this.fileTreeX.setActiveFile(null);
  };

  private readonly resetSteppedItem = () => {
    this.fileTreeX.setActiveFile(null);
  };

  private readonly copyEntry = () => {
    const currentPseudoActive = this.fileTreeX.getActiveFile();
    this.events.dispatch(FileTreeXEvent.onTreeEvents, null, 'copied', currentPseudoActive);
  };
}
