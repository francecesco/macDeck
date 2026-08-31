import Foundation
import MacDeckCore

enum Rete {
    static func risponde(_ base: URL) async -> Bool {
        var r = URLRequest(url: base.appendingPathComponent("api/config"))
        r.timeoutInterval = 2.0
        guard let (_, resp) = try? await URLSession.shared.data(for: r),
              let http = resp as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }

    static func salute(_ base: URL) async -> Salute? {
        var r = URLRequest(url: base.appendingPathComponent("api/health"))
        r.timeoutInterval = 3.0
        guard let (dati, _) = try? await URLSession.shared.data(for: r)
        else { return nil }
        return try? Salute.leggi(dati)
    }

    /// Manda l'evento grezzo all'agent e torna con la combinazione canonica.
    ///
    /// Qui non c'e' nessuna tabella di tasti: keyCode e modificatori vanno
    /// cosi' come sono, e la traduzione la fa keymap.py dall'altra parte.
    static func canonicalizza(base: URL, keyCode: Int,
                              modificatori: [String],
                              caratteri: String) async -> String? {
        var r = URLRequest(url: base.appendingPathComponent("api/keys-canon"))
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: [
            "keyCode": keyCode, "modifiers": modificatori, "chars": caratteri,
        ])
        guard let (dati, resp) = try? await URLSession.shared.data(for: r),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let o = try? JSONSerialization.jsonObject(with: dati)
                  as? [String: Any]
        else { return nil }
        return o["keys"] as? String
    }
}

enum Servizio {
    static let etichetta = "io.macdeck.agent"

    private static func launchctl(_ argomenti: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = argomenti
        let tubo = Pipe()
        p.standardOutput = tubo
        p.standardError = tubo
        guard (try? p.run()) != nil else { return (-1, "") }
        let dati = tubo.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: dati, as: UTF8.self))
    }

    static func caricato() -> Bool {
        launchctl(["print", "gui/\(getuid())/\(etichetta)"]).0 == 0
    }

    static func kickstart() -> Bool {
        launchctl(["kickstart", "-k", "gui/\(getuid())/\(etichetta)"]).0 == 0
    }

    /// Quando l'agent non parte, il motivo e' nel log. Una pagina bianca no.
    ///
    /// Il LaunchAgent (vedi `agent/macdeck/cli.py`) manda stdout su
    /// `macdeck.log` e stderr su `macdeck.err`: un traceback Python finisce
    /// sempre su stderr, mentre stdout raccoglie solo i banner d'avvio
    /// riusciti. Leggere solo `.log` mostrerebbe le partenze buone e
    /// nasconderebbe proprio il motivo del fallimento — per questo stderr
    /// viene prima, ed entrambi i file sono letti anche se uno manca.
    static func codaDelLog() -> String {
        let file: [(percorso: String, etichetta: String)] = [
            ("/tmp/macdeck.err", "macdeck.err"),
            ("/tmp/macdeck.log", "macdeck.log"),
        ]
        let pezzi = file.map { percorso, etichetta -> String in
            guard let testo = try? String(contentsOfFile: percorso,
                                          encoding: .utf8),
                  !testo.isEmpty
            else { return "── \(etichetta) ──\n(vuoto o non leggibile)" }
            let coda = testo.split(separator: "\n",
                                   omittingEmptySubsequences: false)
                .suffix(30).joined(separator: "\n")
            return "── \(etichetta) ──\n\(coda)"
        }
        return pezzi.joined(separator: "\n\n")
    }
}
