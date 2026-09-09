/////////////////////////////////////////////////////////////
//
// CDEadmin - Multi-engine Database Administration
//
// Copyright (C) 2013 - 2026, The pgAdmin Development Team
// This software is released under the PostgreSQL Licence
//
//////////////////////////////////////////////////////////////


export default function reactAspenOverride(theme) {
  const reducedMotionLoader =
    'html[data-cdeadmin-motion="reduced"] '
    + '.file-entry button.directory-toggle.loading';
  const forcedColorGuides =
    '.file-entry span.tree-branch-segment:before, '
    + '.file-entry span.tree-branch-segment:after, '
    + '.file-entry span.tree-node-terminal.leaf:before';
  return {
    '.drag-tree-node': {
      position: 'absolute',
      top: '-100px',
      left: 0,
      zIndex: 99999,
      color: theme.otherVars.tree.textFg,
      background: theme.otherVars.tree.inputBg,
      padding: '0.25rem 0.75rem',
      maxWidth: '30%',
      overflow: 'hidden',
      whiteSpace: 'nowrap',
      textOverflow: 'ellipsis',
    },

    '.file-tree': {
      color: theme.otherVars.tree.textFg + ' !important',
      backgroundColor: theme.otherVars.tree.inputBg + ' !important',
      fontFamily: theme.typography.fontFamily + ' !important',
      fontSize: '0.815rem' + ' !important',
      display: 'inline-block',
      position: 'relative',
      width: '100%',
      '&, & *': {
        boxSizing: 'border-box',
      },
    },

    '.browser-tree': {
      height: '100%',
    },

    '@keyframes cde-tree-spin': {
      to: {transform: 'rotate(360deg)'},
    },

    [reducedMotionLoader]: {
      animation: 'none',
      backgroundColor: theme.palette.primary.main,
      borderColor: theme.palette.primary.main,
      opacity: 0.7,
    },

    '@media (forced-colors: active)': {
      [forcedColorGuides]: {
        backgroundColor: 'CanvasText',
      },
      '.file-entry button.directory-toggle': {
        borderColor: 'CanvasText',
        color: 'CanvasText',
        forcedColorAdjust: 'auto',
      },
    },

    '.file-tree>': {
      div: {
        position: 'absolute' + ' !important',
        height: '100%' + ' !important',
        top: '0px' + ' !important',

        '>div': {
          scrollbarGutter: 'auto',
          overflow: 'overlay' + ' !important',
        },
      },
    },

    '.file-entry': {
      font: 'inherit',
      textAlign: 'left',
      display: 'flex',
      alignItems: 'center',
      whiteSpace: 'nowrap',
      padding: '2px 0',
      minHeight: 'var(--cde-tree-row-height, 28px)',
      paddingLeft: '2px',
      cursor: 'default',

      'span.tree-branch': {
        alignSelf: 'stretch',
        display: 'inline-flex',
        flexShrink: 0,
        height: '100%',
        pointerEvents: 'none',
      },

      'span.tree-branch-segment': {
        display: 'inline-block',
        flex: '0 0 var(--cde-tree-indent, 18px)',
        height: '100%',
        position: 'relative',
        width: 'var(--cde-tree-indent, 18px)',

        '&.ancestor.continues:before, &.current:before': {
          backgroundColor: 'var(--cde-tree-guide-color, '
            + theme.otherVars.tree.textFg + ')',
          content: '""',
          left: '50%',
          position: 'absolute',
          top: '-2px',
          width: 'var(--cde-tree-guide-width, 1px)',
          height: 'calc(100% + 4px)',
        },

        '&.current.is-last:before': {
          height: 'calc(50% + 2px)',
        },

        '&.current:after': {
          backgroundColor: 'var(--cde-tree-guide-color, '
            + theme.otherVars.tree.textFg + ')',
          content: '""',
          height: 'var(--cde-tree-guide-width, 1px)',
          left: '50%',
          position: 'absolute',
          top: '50%',
          width: 'calc(50% + '
            + '(var(--cde-tree-expander-size, 16px) / 2) + 2px)',
        },
      },

      'button.directory-toggle, span.tree-node-terminal': {
        flex: '0 0 var(--cde-tree-expander-size, 16px)',
        height: 'var(--cde-tree-expander-size, 16px)',
        margin: '0 2px',
        position: 'relative',
        width: 'var(--cde-tree-expander-size, 16px)',
        zIndex: 1,
      },

      'button.directory-toggle': {
        appearance: 'none',
        backgroundColor: theme.otherVars.tree.inputBg,
        border: 'var(--cde-tree-guide-width, 1px) solid '
          + 'var(--cde-tree-guide-color, ' + theme.palette.grey[500] + ')',
        borderRadius: '2px',
        color: theme.otherVars.tree.textFg,
        cursor: 'pointer',
        padding: 0,

        '&:before, &:after': {
          backgroundColor: 'currentColor',
          content: '""',
          left: '25%',
          position: 'absolute',
          top: 'calc(50% - (var(--cde-tree-guide-width, 1px) / 2))',
          width: '50%',
          height: 'var(--cde-tree-guide-width, 1px)',
        },

        '&:after': {
          left: 'calc(50% - (var(--cde-tree-guide-width, 1px) / 2))',
          top: '25%',
          width: 'var(--cde-tree-guide-width, 1px)',
          height: '50%',
        },

        '&.open:after': {
          display: 'none',
        },

        '&.loading': {
          borderColor: 'transparent',
          borderRadius: '50%',
          borderTopColor: theme.palette.primary.main,
          animation: 'cde-tree-spin var(--cde-motion-slow, 300ms) linear infinite',
          '&:before, &:after': {
            display: 'none',
          },
        },

        '&:focus-visible': {
          outline: 'var(--cde-focus-width, 2px) solid '
            + 'var(--cde-color-focus, ' + theme.palette.primary.main + ')',
          outlineOffset: 'var(--cde-focus-offset, 1px)',
        },
      },

      'span.tree-node-terminal.leaf:before': {
        backgroundColor: 'var(--cde-tree-guide-color, '
          + theme.otherVars.tree.textFg + ')',
        borderRadius: '50%',
        content: '""',
        height: 'calc(2px + var(--cde-tree-guide-width, 1px))',
        left: 'calc(50% - 1px)',
        position: 'absolute',
        top: 'calc(50% - 1px)',
        width: 'calc(2px + var(--cde-tree-guide-width, 1px))',
      },

      'input.tree-node-check': {
        accentColor: theme.palette.primary.main,
        cursor: 'pointer',
        flex: '0 0 auto',
        height: 'calc(var(--cde-tree-expander-size, 16px) - 2px)',
        margin: '0 4px 0 1px',
        width: 'calc(var(--cde-tree-expander-size, 16px) - 2px)',
      },

      '&.disabled': {
        cursor: 'not-allowed',
        opacity: 0.58,
      },

      '&.big': {
        fontFamily: 'monospace',
      },

      '&:hover, &.pseudo-active': {
        color: theme.otherVars.tree.textHoverFg + ' !important',
        backgroundColor: theme.otherVars.tree.bgHover + ' !important',
        'span.file-label': {
          'span.file-name': {
            color: theme.otherVars.tree.textHoverFg,
          },
          'span.children-count': {
            color: theme.otherVars.tree.textHoverFg
          },
        },
      },

      '&.active, &.prompt': {
        color: theme.otherVars.tree.textHoverFg + ' !important',
        backgroundColor: theme.otherVars.tree.bgSelected + ' !important',
        borderColor: theme.otherVars.tree.bgSelected,
        borderRight: '3px solid ' + theme.palette.primary.main + ' !important',
        'span.file-label': {
          'span.file-name': {
            color: theme.otherVars.textHoverFg,
          },
        },
      },

      'span.file-label': {
        display: 'flex',
        gap: '2px',
        alignItems: 'center',
        padding: '0 2px 0 2px',
        border: '1px solid transparent',
        height: 'auto',
        whiteSpace: 'normal',
        cursor: 'pointer !important',
        marginLeft: '2px',
        '&:hover, &.pseudo-active': {
          color: theme.otherVars.tree.fgHover,
        },
      },

      'span.file-name': {
        font: 'inherit',
        flexGrow: 1,
        userSelect: 'none',
        cursor: 'pointer !important',
        whiteSpace: 'nowrap',
        '&:hover, &.pseudo-active': {
          color: theme.otherVars.tree.fgHover,
        },
      },

      'span.children-count': {
        '&:hover, &.pseudo-active': {
          color: theme.otherVars.tree.fgHover,
        },
      },

      'div.file-tag': {
        color: 'var(--tag-color)',
        border: '1px solid color-mix(in srgb, var(--tag-color) 90%, #fff)',
        padding: '0px 4px',
        borderRadius: theme.shape.borderRadius,
        backgroundColor: 'color-mix(in srgb, color-mix(in srgb, var(--tag-color) 10%, #fff) 50%, transparent);',
        lineHeight: 1.2,
        whiteSpace: 'nowrap'
      },

      i: {
        display: 'inline-block',
        font: 'normal normal normal 18px/1 "Font Awesome 5 Free"',
        fontSize: 'var(--cde-icon-scale, 18px)',
        textAlign: 'center',
        height: '21px !important',
        width: '20px !important',
        flexShrink: 0,

        '&:before': {
          height: 'inherit',
          width: 'inherit',
          display: 'inline-block',
        },

      },

    },
  };
}
