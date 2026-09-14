import React, {useContext, useLayoutEffect, useRef} from 'react';

// Task controls can veto title-bar, Escape and footer close requests alike.
// Guards may await provider release; false keeps the task and its state open.
export const ModalCloseGuardContext = React.createContext(null);

export function useModalCloseGuard(guard) {
  const register = useContext(ModalCloseGuardContext);
  const current = useRef(guard);
  current.current = guard;
  useLayoutEffect(() => {
    if (!register) return undefined;
    return register(() => current.current());
  }, [register]);
}
