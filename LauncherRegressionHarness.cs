// Console test entry point. Exercises production networking and in-process
// WinForms lifecycle without sending input to another process or user window.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class LauncherRegressionHarness
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            int port = Int32.Parse(args[1]);
            if (args[0] == "probe")
            {
                Stopwatch clock = Stopwatch.StartNew();
                bool listening = Launcher.ServiceIsListening(port);
                Console.WriteLine(new JavaScriptSerializer().Serialize(new { listening = listening, milliseconds = clock.ElapsedMilliseconds }));
                return 0;
            }
            if (args[0] == "stop-idempotent")
            {
                Launcher.StopService(port);
                Launcher.StopService(port);
                if (Launcher.ServiceIsListening(port)) throw new Exception("Inactive service unexpectedly listening");
                Console.WriteLine("inactive-service shutdown twice OK");
                return 0;
            }
            if (args[0] == "context-exit" || args[0] == "context-exit-during-boot")
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.SetUnhandledExceptionMode(UnhandledExceptionMode.ThrowException);
                using (TrayContext context = new TrayContext(args[2], true, true, 0))
                using (System.Windows.Forms.Timer exitTimer = new System.Windows.Forms.Timer())
                {
                    if (args[0] == "context-exit-during-boot")
                    {
                        // Constructor starts Boot asynchronously; close before
                        // any queued UI completion can be processed.
                        context.RequestExit();
                        context.RequestExit();
                    }
                    else
                    {
                        exitTimer.Interval = 250;
                        exitTimer.Tick += delegate { exitTimer.Stop(); context.RequestExit(); };
                        exitTimer.Start();
                    }
                    Application.Run(context);
                }
                if (Launcher.ServiceIsListening(port)) throw new Exception("Context exited while service still listening");
                Console.WriteLine(args[0] + ": message loop exited and service closed OK");
                return 0;
            }
            if (args[0] == "cycle")
            {
                Dictionary<string, object> status = Launcher.Status(port);
                if (status == null) throw new Exception("Initial service status unavailable");
                string token = Convert.ToString(status["token"]);
                Dictionary<string, object> stopped = Launcher.Request(port, "/api/tracking", new Dictionary<string, object> { { "paused", true } }, token);
                if (!(bool)stopped["paused"]) throw new Exception("Stop did not pause tracking");
                status = Launcher.Status(port);
                if (status == null || !(bool)((Dictionary<string, object>)status["tracking"])["paused"])
                    throw new Exception("Pause was not reflected in a fresh status read");
                Dictionary<string, object> started = Launcher.Request(port, "/api/tracking", new Dictionary<string, object> { { "paused", false } }, token);
                if ((bool)started["paused"]) throw new Exception("Start did not resume tracking");
                status = Launcher.Status(port);
                if (status == null || (bool)((Dictionary<string, object>)status["tracking"])["paused"])
                    throw new Exception("Resume was not reflected in a fresh status read");
                Launcher.StopService(port);
                if (Launcher.ServiceIsListening(port)) throw new Exception("Closed service still reported as listening after shutdown");
                Console.WriteLine("stop/start/shutdown/closed-port OK");
                return 0;
            }
            throw new Exception("Unknown harness mode");
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
            return 1;
        }
    }
}
