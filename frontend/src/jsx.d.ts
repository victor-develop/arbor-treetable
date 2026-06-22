// React 19 migration shim.
//
// @types/react@19 removed the GLOBAL `JSX` namespace; it now lives under
// `React.JSX`. The Arbor components use the automatic JSX runtime (no `import
// React`) and annotate return types as `JSX.Element`. Rather than add a React
// import to every component, re-expose the global `JSX` namespace as an alias of
// `React.JSX` in one place. Pure types — no runtime cost.
import type { JSX as ReactJSX } from "react";

declare global {
  namespace JSX {
    type Element = ReactJSX.Element;
    type ElementClass = ReactJSX.ElementClass;
    type ElementAttributesProperty = ReactJSX.ElementAttributesProperty;
    type ElementChildrenAttribute = ReactJSX.ElementChildrenAttribute;
    type LibraryManagedAttributes<C, P> = ReactJSX.LibraryManagedAttributes<C, P>;
    type IntrinsicAttributes = ReactJSX.IntrinsicAttributes;
    type IntrinsicClassAttributes<T> = ReactJSX.IntrinsicClassAttributes<T>;
    type IntrinsicElements = ReactJSX.IntrinsicElements;
  }
}
