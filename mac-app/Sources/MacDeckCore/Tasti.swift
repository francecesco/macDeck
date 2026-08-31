/// I quattro modificatori che l'agent conosce, coi bit di NSEvent.
///
/// L'ordine dell'elenco e' quello canonico che keymap.py si aspetta:
/// cmd, ctrl, opt, shift. Qui finisce la conoscenza del guscio sui tasti —
/// il nome del tasto premuto lo decide Python, da /api/keys-canon.
private let noti: [(bit: UInt, nome: String)] = [
    (1 << 20, "cmd"),
    (1 << 18, "ctrl"),
    (1 << 19, "opt"),
    (1 << 17, "shift"),
]

public func modificatori(daFlag flag: UInt) -> [String] {
    noti.filter { flag & $0.bit != 0 }.map(\.nome)
}
