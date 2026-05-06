; =============================================================================
; repowise — Pascal / Delphi symbol and import queries
; tree-sitter-pascal >= 0.9
;
; Capture name conventions (shared across ALL language query files):
;   @symbol.def       — the full definition node (used for line numbers, kind)
;   @symbol.name      — the name identifier node
;   @symbol.params    — parameter list node (optional)
;   @symbol.modifiers — decorator / visibility modifier nodes (optional)
;   @import.statement — the full import node
;   @import.module    — the module path being imported from
;
; Verified against tree-sitter-pascal 0.9.1 grammar. Key facts:
;   - declType has a named field 'name' (_genericName)
;   - _declProc (transparent, inlined into declProc) has named fields
;     'name' (_genericName) and 'args' (declArgs)
;   - defProc has a named field 'header' (declProc) for the function header
;   - declUses children are moduleName nodes (no named fields on children)
;   - Qualified method names in implementation use genericDot ("TBar.Create")
; =============================================================================

; ---------------------------------------------------------------------------
; Type declarations (class, record, interface, enum)
; The 'name' field holds an identifier or genericDot (generic type).
; ---------------------------------------------------------------------------

(declType
  name: (_) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Procedure / function declarations
;
; Interface-section class members appear as standalone (declProc) nodes
; without a (defProc) wrapper. Implementation-section definitions appear as
; (defProc (header: (declProc ...)) (block ...)).
;
; We capture both so that:
;   - Pure-interface units (header files, abstract classes) still yield symbols
;   - Typical units show both declarations (short name) and implementations
;     (qualified name like TBar.Create)
; ---------------------------------------------------------------------------

; Interface-section declarations (class members, standalone forward decls)
(declSection
  (declProc
    name: (_) @symbol.name
    args: (declArgs)? @symbol.params
  ) @symbol.def
)

; Standalone interface-section procedures (not inside a class body)
(interface
  (declProc
    name: (_) @symbol.name
    args: (declArgs)? @symbol.params
  ) @symbol.def
)

; Implementation-section function / procedure / constructor / destructor bodies
(defProc
  header: (declProc
    name: (_) @symbol.name
    args: (declArgs)? @symbol.params
  )
) @symbol.def

; ---------------------------------------------------------------------------
; Uses clauses (imports)
;
; Both interface-section and implementation-section uses clauses are captured.
; Each (moduleName) child is one unit reference (may be dotted: System.SysUtils).
; ---------------------------------------------------------------------------

(declUses
  (moduleName) @import.module
) @import.statement
