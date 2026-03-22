; PhotoGal NSIS installer hooks
; Prevents stale CUDA DLLs from surviving across updates.
; When a user downloads the CUDA addon, extra DLLs are added to sidecar/.
; The default NSIS upgrade overwrites known files but leaves unknown extras.
; This hook removes the entire sidecar dir before the new files are copied.

!macro NSIS_HOOK_PREINSTALL
  RMDir /r "$INSTDIR\sidecar"
!macroend
