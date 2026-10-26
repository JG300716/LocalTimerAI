// Build with BuildLauncher.ps1. No NuGet packages; Windows .NET Framework 4.x.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("LocalTimerAI / LocalFlow")]
[assembly: System.Reflection.AssemblyProduct("LocalTimerAI")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("1.0.0.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("1.0.0")]

internal static class Launcher
{
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyIcon(IntPtr icon);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    internal static extern uint RegisterWindowMessage(string message);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    internal static extern bool SetForegroundWindow(IntPtr window);

    [STAThread]
    private static int Main(string[] arguments)
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        bool noBrowser = Array.IndexOf(arguments, "--no-browser") >= 0;
        bool background = Array.IndexOf(arguments, "--background") >= 0;
        if (Array.IndexOf(arguments, "--self-test") >= 0)
        {
            using (Icon icon = MakeIcon())
                return icon.Width > 0 && ReadPort(folder) >= 1024 ? 0 : 1;
        }
        string identity;
        using (SHA256 hash = SHA256.Create())
            identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(folder.ToUpperInvariant()))).Replace("-", "").Substring(0, 24);
        uint showMessage = RegisterWindowMessage("LocalFlow.ShowWindow." + identity);
        bool created;
        using (Mutex mutex = new Mutex(true, "Local\\LocalFlowTray_" + identity, out created))
        {
            if (!created)
            {
                if (!background) PostMessage(new IntPtr(0xffff), showMessage, IntPtr.Zero, IntPtr.Zero);
                if (!noBrowser) OpenBrowser(ReadPort(folder));
                return 0;
            }
            try
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (TrayContext context = new TrayContext(folder, noBrowser, background, showMessage))
                    Application.Run(context);
                return 0;
            }
            catch (Exception error)
            {
                MessageBox.Show("Nie udało się uruchomić LocalFlow.\n\n" + error.Message,
                                "LocalFlow", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    internal static int ReadPort(string folder)
    {
        try
        {
            Dictionary<string, object> config = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(folder, "config.json")));
            Dictionary<string, object> server = (Dictionary<string, object>)config["server"];
            int port = Convert.ToInt32(server["port"]);
            if (port >= 1024 && port <= 65535) return port;
        }
        catch { }
        return 48763;
    }

    internal static string Url(int port) { return "http://127.0.0.1:" + port; }

    internal static void OpenBrowser(int port, string page = "/?mode=live#overview")
    {
        string address = Url(port) + page;
        try { Process.Start(new ProcessStartInfo(address) { UseShellExecute = true }); }
        catch (Exception error) { MessageBox.Show(address + "\n\n" + error.Message, "LocalFlow"); }
    }

    internal static Dictionary<string, object> Request(int port, string path, object data, string token)
    {
        HttpWebRequest request = (HttpWebRequest)WebRequest.Create(Url(port) + path);
        request.Proxy = null;
        request.Timeout = 2000;
        request.ReadWriteTimeout = 2000;
        request.AllowAutoRedirect = false;
        if (data != null)
        {
            request.Method = "POST";
            request.ContentType = "application/json";
            request.Headers["X-LocalFlow-Token"] = token;
            byte[] payload = Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(data));
            request.ContentLength = payload.Length;
            using (Stream stream = request.GetRequestStream()) stream.Write(payload, 0, payload.Length);
        }
        using (WebResponse response = request.GetResponse())
        using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
    }

    internal static Dictionary<string, object> Status(int port)
    {
        try
        {
            Dictionary<string, object> status = Request(port, "/api/status", null, null);
            object name;
            if (status.TryGetValue("name", out name) && Convert.ToString(name) == "LocalFlow") return status;
        }
        catch { }
        return null;
    }

    // Windows can take about two seconds to report a refused TCP connection.
    // A short connect timeout therefore cannot tell whether this local server
    // is listening. Inspect the OS listener table without opening a connection.
    internal static bool ServiceIsListening(int port)
    {
        foreach (IPEndPoint endpoint in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
        {
            if (endpoint.Port == port &&
                (endpoint.Address.Equals(IPAddress.Loopback) || endpoint.Address.Equals(IPAddress.Any) ||
                 endpoint.Address.Equals(IPAddress.IPv6Any))) return true;
        }
        return false;
    }

    internal static void StopService(int port)
    {
        if (!ServiceIsListening(port)) return;
        Dictionary<string, object> status = Status(port);
        if (status == null)
        {
            if (!ServiceIsListening(port)) return;
            throw new IOException("Usługa nie odpowiada, ale jej port jest nadal otwarty. Nie można potwierdzić zakończenia.");
        }
        Dictionary<string, object> result = Request(port, "/api/shutdown", new Dictionary<string, object>(), Convert.ToString(status["token"]));
        object acknowledged;
        if (!result.TryGetValue("ok", out acknowledged) || !(acknowledged is bool) || !(bool)acknowledged)
            throw new IOException("Usługa nie potwierdziła polecenia zakończenia.");
        DateTime deadline = DateTime.UtcNow.AddSeconds(10);
        while (ServiceIsListening(port) && DateTime.UtcNow < deadline) Thread.Sleep(100);
        if (ServiceIsListening(port)) throw new IOException("Usługa nie zakończyła pracy. Spróbuj ponownie kliknąć Exit.");
    }

    internal static Process StartScript(string folder, string arguments)
    {
        ProcessStartInfo start = new ProcessStartInfo();
        start.FileName = Environment.GetEnvironmentVariable("ComSpec") ?? Path.Combine(Environment.SystemDirectory, "cmd.exe");
        start.Arguments = "/d /s /c \"\"" + Path.Combine(folder, "Start.cmd") + "\" " + arguments + "\"";
        start.WorkingDirectory = folder;
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.WindowStyle = ProcessWindowStyle.Hidden;
        return Process.Start(start);
    }

    internal static Icon MakeIcon()
    {
        using (Bitmap image = new Bitmap(32, 32))
        using (Graphics graphic = Graphics.FromImage(image))
        using (SolidBrush face = new SolidBrush(Color.FromArgb(31, 173, 145)))
        using (Pen stroke = new Pen(Color.White, 2.3f))
        {
            graphic.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            graphic.Clear(Color.Transparent);
            graphic.FillEllipse(face, 1, 1, 30, 30);
            graphic.DrawEllipse(stroke, 6, 6, 20, 20);
            graphic.DrawLine(stroke, 16, 10, 16, 16);
            graphic.DrawLine(stroke, 16, 16, 21, 19);
            IntPtr handle = image.GetHicon();
            try { using (Icon temporary = Icon.FromHandle(handle)) return (Icon)temporary.Clone(); }
            finally { DestroyIcon(handle); }
        }
    }
}

internal sealed class TrackerWindow : Form
{
    private readonly uint showMessage;

    internal TrackerWindow(uint message)
    {
        showMessage = message;
        Text = "LocalFlow — uruchamianie";
        ShowInTaskbar = true;
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        MinimizeBox = true;
        AutoScaleMode = AutoScaleMode.Font;
        Font = new Font("Segoe UI", 9.5f);
        BackColor = Color.FromArgb(17, 22, 31);
        ForeColor = Color.FromArgb(230, 236, 244);
        ClientSize = new Size(580, 438);
        AccessibleName = "LocalFlow — kontrola trackera";
    }

    internal void RestoreWindow()
    {
        Show();
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        Activate();
        BringToFront();
        Launcher.SetForegroundWindow(Handle);
    }

    protected override void WndProc(ref Message message)
    {
        if (showMessage != 0 && message.Msg == showMessage)
        {
            RestoreWindow();
            message.Result = new IntPtr(1);
            return;
        }
        base.WndProc(ref message);
    }
}

internal sealed class TrayContext : ApplicationContext
{
    private readonly string folder;
    private readonly int port;
    private readonly TrackerWindow window;
    private readonly NotifyIcon tray;
    private readonly Icon icon;
    private readonly ContextMenuStrip menu;
    private readonly ToolStripMenuItem stateItem;
    private readonly ToolStripMenuItem startItem;
    private readonly ToolStripMenuItem stopItem;
    private readonly ToolStripMenuItem exitItem;
    private readonly Button startButton;
    private readonly Button stopButton;
    private readonly Button exitButton;
    private readonly Label statusLabel;
    private readonly Label detailLabel;
    private readonly Label errorLabel;
    private readonly System.Windows.Forms.Timer statusTimer;
    private volatile bool disposed;
    private bool busy;
    private bool polling;
    private bool allowClose;
    private bool exitRequested;
    private int generation;
    private Dictionary<string, object> lastStatus;

    internal TrayContext(string folder, bool noBrowser, bool background, uint showMessage)
    {
        this.folder = folder;
        port = Launcher.ReadPort(folder);
        window = new TrackerWindow(showMessage);
        icon = Launcher.MakeIcon();
        window.Icon = icon;
        AddLabel(window, "LocalFlow", 24, 18, 532, 34, 22, Color.White);
        AddLabel(window, "Lokalny tracker aktywności", 26, 56, 530, 24, 10, Color.FromArgb(150, 164, 185));
        Panel statusPanel = new Panel { Location = new Point(24, 92), Size = new Size(532, 102), BackColor = Color.FromArgb(27, 35, 48) };
        window.Controls.Add(statusPanel);
        statusLabel = AddLabel(statusPanel, "Uruchamianie…", 16, 13, 500, 29, 15, Color.FromArgb(120, 223, 189));
        detailLabel = AddLabel(statusPanel, Launcher.Url(port), 16, 49, 500, 44, 9.5f, Color.FromArgb(173, 187, 205));

        startButton = AddButton(window, "Start", 24, 212, 164, 40, delegate { SetTracking(false); }, true);
        stopButton = AddButton(window, "Stop", 208, 212, 164, 40, delegate { SetTracking(true); }, false);
        exitButton = AddButton(window, "Exit", 392, 212, 164, 40, delegate { RequestExit(); }, false);
        AddButton(window, "Dashboard", 24, 272, 164, 34, delegate { Launcher.OpenBrowser(port); }, false);
        AddButton(window, "Podgląd stylów", 208, 272, 164, 34, delegate { Launcher.OpenBrowser(port); }, false);
        AddButton(window, "Ustawienia", 392, 272, 164, 34, delegate { Launcher.OpenBrowser(port, "/?mode=live#settings"); }, false);
        AddLabel(window, "Start i Stop sterują pomiarem aplikacji .exe. WakaTime i AI działają osobno. Exit zatrzymuje całą usługę LocalFlow.", 24, 326, 532, 42, 9, Color.FromArgb(150, 164, 185));
        errorLabel = AddLabel(window, "", 24, 378, 532, 48, 9, Color.FromArgb(255, 162, 151));
        errorLabel.AccessibleName = "Błąd LocalFlow";
        window.FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (allowClose) return;
            e.Cancel = true;
            RequestExit();
        };
        window.FormClosed += delegate { ExitThread(); };
        // Registered messages can restore a tray-only instance too.
        IntPtr windowHandle = window.Handle;

        menu = new ContextMenuStrip();
        stateItem = new ToolStripMenuItem("Uruchamianie LocalFlow…") { Enabled = false };
        menu.Items.Add(stateItem);
        menu.Items.Add("Pokaż okno LocalFlow", null, delegate { window.RestoreWindow(); });
        menu.Items.Add(new ToolStripSeparator());
        startItem = new ToolStripMenuItem("Start", null, delegate { SetTracking(false); });
        stopItem = new ToolStripMenuItem("Stop", null, delegate { SetTracking(true); });
        exitItem = new ToolStripMenuItem("Exit", null, delegate { RequestExit(); });
        menu.Items.Add(startItem);
        menu.Items.Add(stopItem);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Otwórz dashboard — moje dane", null, delegate { Launcher.OpenBrowser(port); });
        menu.Items.Add("Podgląd stylów UI", null, delegate { Launcher.OpenBrowser(port); });
        menu.Items.Add("Ustawienia / podłącz WakaTime", null, delegate { Launcher.OpenBrowser(port, "/?mode=live#settings"); });
        menu.Items.Add("Edytuj konfigurację .exe", null, delegate
        {
            try { Process.Start(new ProcessStartInfo("notepad.exe", "\"" + Path.Combine(folder, "config.json") + "\"") { UseShellExecute = false }); }
            catch (Exception error) { ShowError(error); }
        });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(exitItem);
        tray = new NotifyIcon { Icon = icon, Text = "LocalFlow — uruchamianie", Visible = true, ContextMenuStrip = menu };
        tray.DoubleClick += delegate { window.RestoreWindow(); };
        Application.ApplicationExit += OnApplicationExit;

        statusTimer = new System.Windows.Forms.Timer { Interval = 3000 };
        statusTimer.Tick += delegate { PollStatus(); };
        statusTimer.Start();
        ApplyStatus(null);
        if (!background) window.Show();
        Boot(!noBrowser);
    }

    private static Label AddLabel(Control parent, string text, int x, int y, int width, int height, float size, Color color)
    {
        Label label = new Label { Text = text, Location = new Point(x, y), Size = new Size(width, height),
            Font = new Font("Segoe UI", size), ForeColor = color, BackColor = Color.Transparent };
        parent.Controls.Add(label);
        return label;
    }

    private static Button AddButton(Control parent, string text, int x, int y, int width, int height, EventHandler action, bool primary)
    {
        Button button = new Button { Text = text, AccessibleName = text, Location = new Point(x, y), Size = new Size(width, height),
            FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand, UseVisualStyleBackColor = false,
            BackColor = primary ? Color.FromArgb(35, 123, 104) : Color.FromArgb(33, 43, 58), ForeColor = Color.White };
        button.FlatAppearance.BorderColor = primary ? Color.FromArgb(65, 167, 139) : Color.FromArgb(60, 76, 98);
        button.Click += action;
        parent.Controls.Add(button);
        return button;
    }

    private static bool Flag(Dictionary<string, object> values, string key, bool fallback)
    {
        object value;
        return values != null && values.TryGetValue(key, out value) && value is bool ? (bool)value : fallback;
    }

    private static Dictionary<string, object> Tracking(Dictionary<string, object> status)
    {
        object tracking;
        return status != null && status.TryGetValue("tracking", out tracking) ? tracking as Dictionary<string, object> : null;
    }

    private void OnUI(Action action)
    {
        if (disposed || window.IsDisposed) return;
        try { window.BeginInvoke(action); }
        catch (InvalidOperationException) { }
    }

    private void ApplyStatus(Dictionary<string, object> status)
    {
        lastStatus = status;
        Dictionary<string, object> tracking = Tracking(status);
        bool supported = Flag(tracking, "supported", true);
        bool enabled = Flag(tracking, "enabled", true);
        bool running = Flag(tracking, "running", false);
        bool paused = Flag(tracking, "paused", false);
        bool preview = Flag(status, "no_tracking", false);
        string caption = status == null ? "Usługa niedostępna" : !supported ? "Pomiar .exe niedostępny" :
            preview ? "Tryb podglądu — pomiar wyłączony" : !enabled ? "Pomiar wyłączony w konfiguracji" :
            !running ? "Kolektor .exe nie działa" : paused ? "Zatrzymano pomiar .exe" : "Pomiar .exe aktywny";
        statusLabel.Text = caption;
        statusLabel.ForeColor = status != null && running && !paused && enabled && !preview ? Color.FromArgb(120, 223, 189) : Color.FromArgb(239, 196, 115);
        stateItem.Text = caption;
        window.Text = "LocalFlow — " + caption;
        tray.Text = ("LocalFlow — " + caption).Substring(0, Math.Min(63, ("LocalFlow — " + caption).Length));
        detailLabel.Text = Launcher.Url(port) + (status == null ? "\nKliknij Start, aby uruchomić tracker." : "\nUsługa lokalna odpowiada. Stan odświeżany co 3 sekundy.");
        object error;
        if (tracking != null && tracking.TryGetValue("error", out error) && error != null && Convert.ToString(error).Length > 0)
            detailLabel.Text = Launcher.Url(port) + "\n" + Convert.ToString(error);
        bool canStart = status == null || (supported && enabled && !preview && (!running || paused));
        bool canStop = status != null && supported && enabled && !preview && running && !paused;
        startButton.Enabled = startItem.Enabled = !busy && canStart;
        stopButton.Enabled = stopItem.Enabled = !busy && canStop;
        exitButton.Enabled = exitItem.Enabled = !busy;
    }

    private void BeginOperation(string caption)
    {
        busy = true;
        generation++;
        errorLabel.Text = "";
        ApplyStatus(lastStatus);
        statusLabel.Text = stateItem.Text = caption;
        window.Text = "LocalFlow — " + caption;
    }

    private void FinishOperation(Dictionary<string, object> status)
    {
        busy = false;
        ApplyStatus(status);
        if (exitRequested)
        {
            exitRequested = false;
            RequestExit();
        }
    }

    private void ShowError(Exception error)
    {
        errorLabel.Text = error.Message;
        window.RestoreWindow();
        tray.ShowBalloonTip(6000, "LocalFlow", error.Message, ToolTipIcon.Warning);
    }

    private void PollStatus()
    {
        if (busy || polling || disposed) return;
        polling = true;
        int requestGeneration = generation;
        ThreadPool.QueueUserWorkItem(delegate
        {
            Dictionary<string, object> status = Launcher.Status(port);
            OnUI(delegate
            {
                polling = false;
                if (!busy && requestGeneration == generation) ApplyStatus(status);
            });
        });
    }

    private Dictionary<string, object> EnsureService()
    {
        Dictionary<string, object> status = Launcher.Status(port);
        if (status != null) return status;
        using (Process process = Launcher.StartScript(folder, "--no-browser"))
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(25);
            while (!disposed && DateTime.UtcNow < deadline)
            {
                status = Launcher.Status(port);
                if (status != null) return status;
                if (process != null && process.HasExited && process.ExitCode != 0) break;
                Thread.Sleep(250);
            }
        }
        throw new IOException("Usługa nie odpowiada. Sprawdź Pythona i plik data\\localflow.log, a następnie kliknij Start.");
    }

    private void Boot(bool openBrowser)
    {
        if (busy || disposed) return;
        BeginOperation("Uruchamianie…");
        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                Dictionary<string, object> status = EnsureService();
                OnUI(delegate { FinishOperation(status); if (openBrowser && !busy && !exitRequested && !disposed) Launcher.OpenBrowser(port); });
            }
            catch (Exception error)
            {
                OnUI(delegate { FinishOperation(null); ShowError(error); });
            }
        });
    }

    private void SetTracking(bool paused)
    {
        if (busy || disposed) return;
        BeginOperation(paused ? "Zatrzymywanie pomiaru…" : "Wznawianie pomiaru…");
        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                Dictionary<string, object> status = paused ? Launcher.Status(port) : EnsureService();
                if (status == null) throw new IOException("Usługa nie odpowiada. Kliknij Start, aby ją uruchomić.");
                Dictionary<string, object> tracking = Tracking(status);
                if (!Flag(tracking, "enabled", true) || Flag(status, "no_tracking", false))
                    throw new IOException("Pomiar .exe jest wyłączony w konfiguracji lub serwer działa w trybie podglądu. Sprawdź Ustawienia.");
                Launcher.Request(port, "/api/tracking", new Dictionary<string, object> { { "paused", paused } }, Convert.ToString(status["token"]));
                status = Launcher.Status(port);
                if (status == null) throw new IOException("Nie można potwierdzić stanu trackera po zmianie.");
                OnUI(delegate { FinishOperation(status); });
            }
            catch (Exception error)
            {
                OnUI(delegate { FinishOperation(lastStatus); ShowError(error); });
            }
        });
    }

    internal void RequestExit()
    {
        if (disposed || allowClose) return;
        if (busy)
        {
            exitRequested = true;
            statusLabel.Text = "Zakończenie po bieżącej operacji…";
            return;
        }
        BeginOperation("Zatrzymywanie usługi…");
        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                Launcher.StopService(port);
                OnUI(delegate
                {
                    allowClose = true;
                    statusTimer.Stop();
                    tray.Visible = false;
                    window.Close();
                    ExitThread();
                });
            }
            catch (Exception error)
            {
                OnUI(delegate { FinishOperation(lastStatus); ShowError(error); });
            }
        });
    }

    private void OnApplicationExit(object sender, EventArgs arguments) { tray.Visible = false; }

    protected override void Dispose(bool disposing)
    {
        disposed = true;
        if (disposing)
        {
            Application.ApplicationExit -= OnApplicationExit;
            statusTimer.Stop();
            statusTimer.Dispose();
            tray.Visible = false;
            tray.Dispose();
            menu.Dispose();
            window.Dispose();
            icon.Dispose();
        }
        base.Dispose(disposing);
    }
}
