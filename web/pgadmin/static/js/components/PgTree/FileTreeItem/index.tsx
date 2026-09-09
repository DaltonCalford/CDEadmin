/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////

import cn from 'classnames';
import * as React from 'react';
import { ClasslistComposite } from 'aspen-decorations';
import { Directory, FileEntry, IItemRendererProps, ItemType, RenamePromptHandle, FileType, FileOrDir} from 'react-aspen';
import {IFileTreeXTriggerEvents, FileTreeXEvent } from '../types';
import { Notificar } from 'notificar';
import _ from 'lodash';
import DoubleClickHandler from './DoubleClickHandler';
import {
  descriptorFromTreeMetadata,
  treeNodeAriaLabel,
} from 'sources/cdeadmin_ui/navigation/TreeNode';
import {ObjectIcon} from 'sources/cdeadmin_ui/icons';
import {TreeBranchGuides} from './TreeBranchGuides';
interface IItemRendererXProps {
    /**
     * In this implementation, decoration are null when item is `PromptHandle`
     *
     * If you would like decorations for `PromptHandle`s, then get them using `DecorationManager#getDecorations(<target>)`.
     * Where `<target>` can be either `NewFilePromptHandle.parent` or `RenamePromptHandle.target` depending on type of `PromptHandle`
     *
     * To determine the type of `PromptHandle`, use `IItemRendererProps.itemType`
     */
    decorations: ClasslistComposite
    onClick: (ev: React.MouseEvent, item: FileEntry | Directory, type: ItemType) => void
    onContextMenu: (ev: React.MouseEvent, item: FileEntry | Directory) => void
    onMouseEnter: (ev: React.MouseEvent, item: FileEntry | Directory) => void
    onMouseLeave: (ev: React.MouseEvent, item: FileEntry | Directory) => void
    onItemHovered: (ev: React.MouseEvent, item: FileEntry | Directory, type: ItemType) => void
    events: Notificar<FileTreeXEvent>
}

// DO NOT EXTEND FROM PureComponent!!! You might miss critical changes made deep within `item` prop
// as far as efficiency is concerned, `react-aspen` works hard to ensure unnecessary updates are ignored
export class FileTreeItem extends React.Component<IItemRendererXProps & IItemRendererProps> {
  public static getBoundingClientRectForItem(item: FileEntry | Directory): DOMRect {
    const divRef = FileTreeItem.itemIdToRefMap.get(item.id);
    if (divRef) {
      return divRef.getBoundingClientRect();
    }
    return null;
  }

  // ensure this syncs up with what goes in CSS, (em, px, % etc.) and what ultimately renders on the page
  public static readonly renderHeight: number = 28;
  private static readonly itemIdToRefMap: Map<number, HTMLDivElement> = new Map();
  private static readonly refToItemIdMap: Map<number, HTMLDivElement> = new Map();
  private readonly fileTreeEvent: IFileTreeXTriggerEvents;

  constructor(props) {
    super(props);
    // used to apply decoration changes, you're welcome to use setState or other mechanisms as you see fit
    this.forceUpdate = this.forceUpdate.bind(this);
  }

  public render() {
    const { item, itemType, decorations } = this.props;
    const isRenamePrompt = itemType === ItemType.RenamePrompt;
    const isNewPrompt = itemType === ItemType.NewDirectoryPrompt || itemType === ItemType.NewFilePrompt;
    const isDirExpanded = itemType === ItemType.Directory
      ? (item as Directory).expanded
      : itemType === ItemType.RenamePrompt && (item as RenamePromptHandle).target.type === FileType.Directory
        ? ((item as RenamePromptHandle).target as Directory).expanded
        : false;

    const fileOrDir =
            (itemType === ItemType.File ||
                itemType === ItemType.NewFilePrompt ||
                (itemType === ItemType.RenamePrompt && (item as RenamePromptHandle).target.constructor === FileEntry))
              ? 'file'
              : 'directory';

    if (this.props.item.parent?.parent && this.props.item.parent?.path) {
      this.props.item.resolvedPathCache = this.props.item.parent.path + '/' + this.props.item._metadata.data.id;
    }

    const itemChildren = item.children && item.children.length > 0 && item._metadata.data._type.indexOf('coll-') !== -1 ? '(' + item.children.length + ')' : '';
    const extraClasses = item._metadata.data.extraClasses ? item._metadata.data.extraClasses.join(' ') : '';

    const tags = item._metadata.data?.tags ?? [];
    const descriptor = descriptorFromTreeMetadata(
      item._metadata.data,
      item.children?.length ?? 0
    );
    const isSelected = decorations?.classlist?.includes('active') ?? false;
    const siblings = Array.isArray(item.parent?.children) ?
      item.parent.children : [];
    const positionInSet = Math.max(1, siblings.indexOf(item) + 1);
    const setSize = Math.max(1, siblings.length);

    return (
      <DoubleClickHandler onDoubleClick={this.handleDoubleClick} onSingleClick={this.handleClick}>
        <div
          className={cn('file-entry', {
            renaming: isRenamePrompt,
            prompt: isRenamePrompt || isNewPrompt,
            new: isNewPrompt,
            disabled: !descriptor.capabilities.enabled,
          }, fileOrDir, decorations ? decorations.classlist : null, `depth-${item.depth}`, extraClasses)}
          data-depth={item.depth}
          onContextMenu={this.handleContextMenu}
          onDragStart={this.handleDragStartItem}
          onMouseEnter={this.handleMouseEnter}
          onMouseLeave={this.handleMouseLeave}
          onKeyDown={()=>{/* taken care by parent */}}
          role="treeitem"
          aria-label={treeNodeAriaLabel(descriptor)}
          aria-expanded={fileOrDir === 'directory' ? isDirExpanded : undefined}
          aria-selected={isSelected}
          aria-disabled={!descriptor.capabilities.enabled}
          aria-level={Math.max(1, Number(item.depth ?? 1))}
          aria-posinset={positionInSet}
          aria-setsize={setSize}
          data-node-kind={descriptor.kind}
          data-object-type={descriptor.objectType}
          data-provider-id={descriptor.providerId || undefined}
          data-engine-id={descriptor.engineId || undefined}
          // required for rendering context menus when opened through context menu button on keyboard
          ref={this.handleDivRef}
          draggable={descriptor.capabilities.enabled &&
            descriptor.capabilities.draggable}>

          <TreeBranchGuides item={item as FileOrDir} />

          {!isNewPrompt && fileOrDir === 'directory' ?
            <button
              type="button"
              tabIndex={-1}
              className={cn('directory-toggle', isDirExpanded ? 'open' : '')}
              onClick={this.handleToggleClick}
              aria-label={`${isDirExpanded ? 'Collapse' : 'Expand'} ${descriptor.label}`}
              aria-expanded={isDirExpanded}
              title={`${isDirExpanded ? 'Collapse' : 'Expand'} ${descriptor.label}`}
            /> : <span className="tree-node-terminal leaf" aria-hidden="true" />
          }

          {descriptor.check.type !== 'none' ? <input
            className="tree-node-check"
            type={descriptor.check.type}
            name={descriptor.check.group || undefined}
            checked={descriptor.check.checked}
            disabled={descriptor.check.disabled ||
              !descriptor.capabilities.enabled}
            aria-label={descriptor.check.label}
            aria-checked={descriptor.check.indeterminate ? 'mixed' :
              descriptor.check.checked}
            ref={this.handleCheckRef}
            onClick={this.stopCheckPropagation}
            onDoubleClick={this.stopCheckPropagation}
            onChange={this.handleCheckChange}
          /> : null}

          <span className='file-label'>{
            descriptor.iconKey ?
              <ObjectIcon
                className="file-icon"
                decorative
                iconKey={descriptor.iconKey}
                objectType={descriptor.objectType}
              /> : null
          }
          <span className='file-name'>
            { _.unescape(this.props.item.getMetadata('data')._label)}
          </span>
          <span className="text-muted" style={{fontSize: '0.9em', whiteSpace: 'nowrap'}}>
            {_.unescape(this.props.item.getMetadata('data').info_label)}
          </span>
          <span className='children-count'>{itemChildren}</span>
          {tags.map((tag)=>(
            <div key={tag.text} className='file-tag' style={{'--tag-color': tag.color} as React.CSSProperties}>
              {tag.text}
            </div>
          ))}
          </span>
        </div>
      </DoubleClickHandler>
    );
  }

  public componentDidMount() {
    this.events = this.props.events;
    this.props.item.resolvedPathCache = this.props.item.parent.path + '/' + this.props.item._metadata.data.id;
    if (this.props.decorations) {
      this.props.decorations.addChangeListener(this.forceUpdate);
    }
    this.setFileLoaded(this.props.item);
  }

  private readonly setFileLoaded = async (FileOrDir): Promise<void> => {
    this.props.changeDirectoryCount(FileOrDir.parent);
    if(FileOrDir._loaded !== true) {
      this.events.dispatch(FileTreeXEvent.onTreeEvents, window.event, 'added', FileOrDir);
    }
    FileOrDir._loaded = true;
  };

  public componentWillUnmount() {
    if (this.props.decorations) {
      this.props.decorations.removeChangeListener(this.forceUpdate);
    }
  }

  public componentDidUpdate(prevProps: IItemRendererXProps) {
    if (prevProps.decorations) {
      prevProps.decorations.removeChangeListener(this.forceUpdate);
    }
    if (this.props.decorations) {
      this.props.decorations.addChangeListener(this.forceUpdate);
    }
  }

  private readonly handleDivRef = (r: HTMLDivElement) => {
    if (r === null) {
      FileTreeItem.itemIdToRefMap.delete(this.props.item.id);
    } else {
      FileTreeItem.itemIdToRefMap.set(this.props.item.id, r);
      FileTreeItem.refToItemIdMap.set(r, this.props.item);
    }
  };

  private readonly handleCheckRef = (control: HTMLInputElement) => {
    if(control) {
      const data = this.props.item._metadata.data;
      control.indeterminate = data.indeterminate === true ||
        data.check_state === 'mixed' || data.check?.indeterminate === true;
    }
  };

  private readonly stopCheckPropagation = (ev: React.SyntheticEvent) => {
    ev.stopPropagation();
  };

  private readonly handleCheckChange = (ev: React.ChangeEvent<HTMLInputElement>) => {
    ev.stopPropagation();
    const data = this.props.item._metadata.data;
    const checked = ev.currentTarget.checked;
    data.checked = checked;
    data._checked = checked;
    data.indeterminate = false;
    data.check_state = checked ? 'checked' : 'unchecked';
    if(data.check) {
      data.check.checked = checked;
      data.check.indeterminate = false;
    }
    if(ev.currentTarget.type === 'radio' && checked) {
      const group = ev.currentTarget.name;
      const siblings = Array.isArray(this.props.item.parent?.children) ?
        this.props.item.parent.children : [];
      siblings.filter((sibling) => sibling !== this.props.item).forEach(
        (sibling) => {
          const siblingData = sibling._metadata?.data;
          const siblingGroup = siblingData?.check_group ??
            siblingData?.check?.group ?? '';
          if(siblingGroup !== group) {
            return;
          }
          siblingData.checked = false;
          siblingData._checked = false;
          siblingData.check_state = 'unchecked';
          if(siblingData.check) {
            siblingData.check.checked = false;
          }
          const siblingRef = FileTreeItem.itemIdToRefMap.get(sibling.id);
          const siblingControl = siblingRef?.querySelector(
            'input.tree-node-check[type="radio"]'
          ) as HTMLInputElement;
          if(siblingControl) {
            siblingControl.checked = false;
          }
        }
      );
    }
    this.props.events.dispatch(
      FileTreeXEvent.onTreeEvents,
      ev.nativeEvent,
      checked ? 'checked' : 'unchecked',
      this.props.item
    );
    this.forceUpdate();
  };

  private readonly handleContextMenu = (ev: React.MouseEvent) => {
    const { item, itemType, onContextMenu } = this.props;
    if (itemType === ItemType.File || itemType === ItemType.Directory) {
      onContextMenu(ev, item as FileOrDir);
    }
  };

  private readonly handleClick = (ev: React.MouseEvent) => {
    const { item, itemType, onClick } = this.props;
    if (itemType === ItemType.File || itemType === ItemType.Directory) {
      onClick(ev, item as FileEntry, itemType);
    }
  };

  private readonly handleToggleClick = (ev: React.MouseEvent) => {
    // The expander is a real one-action control.  Do not pass it through the
    // delayed row single/double-click discriminator: doing so can leave a
    // restored provider node visually open while its lazy children were
    // never requested.
    ev.preventDefault();
    ev.stopPropagation();
    this.handleClick(ev);
  };

  private readonly handleDoubleClick = (ev: React.MouseEvent) => {
    const { item, itemType, onDoubleClick } = this.props;
    if (itemType === ItemType.File || itemType === ItemType.Directory) {
      onDoubleClick(ev, item as FileEntry, itemType);
    }
  };

  private readonly handleMouseEnter = (ev: React.MouseEvent) => {
    const { item, itemType, onMouseEnter } = this.props;
    if (itemType === ItemType.File || itemType === ItemType.Directory) {
      onMouseEnter?.(ev, item as FileEntry);
    }
  };

  private readonly handleMouseLeave = (ev: React.MouseEvent) => {
    const { item, itemType, onMouseLeave } = this.props;
    if (itemType === ItemType.File || itemType === ItemType.Directory) {
      onMouseLeave?.(ev, item as FileEntry);
    }
  };

  private readonly handleDragStartItem = (e: React.DragEvent) => {
    const { item, itemType, events } = this.props;
    if (itemType === ItemType.File || itemType === ItemType.Directory) {
      const ref = FileTreeItem.itemIdToRefMap.get(item.id);
      if (ref) {
        events.dispatch(FileTreeXEvent.onTreeEvents, e, 'dragstart', item);
      }
    }
  };
}
