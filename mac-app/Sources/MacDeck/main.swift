import AppKit

let app = NSApplication.shared
let delegato = Finestra()
app.delegate = delegato
app.setActivationPolicy(.regular)
app.run()
