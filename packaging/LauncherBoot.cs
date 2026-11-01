using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace PyMCLauncher
{
    internal static class Program
    {
        private const string PayloadName = "payload.zip";
        private const string AppTitle = "PyMC Launcher";

        [STAThread]
        private static int Main(string[] args)
        {
            bool selfTest = false;
            foreach (string arg in args)
            {
                if (string.Equals(arg, "--selftest", StringComparison.OrdinalIgnoreCase))
                    selfTest = true;
            }

            try
            {
                string exePath = Assembly.GetExecutingAssembly().Location;
                string exeDir = Path.GetDirectoryName(exePath);
                if (string.IsNullOrEmpty(exeDir))
                    exeDir = AppDomain.CurrentDomain.BaseDirectory;

                string cacheRoot = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "PyMCLauncher");
                Directory.CreateDirectory(cacheRoot);

                string hash = PayloadHash();
                string runtimeDir = Path.Combine(cacheRoot, "runtime-" + hash.Substring(0, 12));
                EnsureExtracted(runtimeDir, hash);

                string dataDir;
                if (selfTest)
                {
                    dataDir = Path.Combine(Path.GetTempPath(), "PyMCLauncher-selftest");
                    Directory.CreateDirectory(dataDir);
                }
                else
                {
                    dataDir = PrepareDataDir(exeDir, cacheRoot);
                }

                Log("runtime=" + runtimeDir);
                Log("data=" + dataDir);

                if (selfTest)
                    return RunSelfTest(runtimeDir, dataDir);

                StartLauncher(runtimeDir, dataDir);
                return 0;
            }
            catch (Exception ex)
            {
                Log("fatal: " + ex);
                if (!selfTest)
                {
                    MessageBox.Show(
                        "PyMC Launcher 启动失败：\n\n" + ex.Message +
                        "\n\n日志：" + BootLogPath(),
                        AppTitle, MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return 1;
            }
        }

        private static string PayloadHash()
        {
            using (Stream stream = Assembly.GetExecutingAssembly()
                       .GetManifestResourceStream(PayloadName))
            {
                if (stream == null)
                    throw new InvalidOperationException("缺少内置 payload.zip");
                using (SHA256 sha = SHA256.Create())
                {
                    byte[] hash = sha.ComputeHash(stream);
                    StringBuilder sb = new StringBuilder(hash.Length * 2);
                    foreach (byte b in hash)
                        sb.Append(b.ToString("x2"));
                    return sb.ToString();
                }
            }
        }

        private static void EnsureExtracted(string runtimeDir, string hash)
        {
            string marker = Path.Combine(runtimeDir, ".ready");
            if (File.Exists(marker))
                return;

            using (Mutex mutex = new Mutex(false, "PyMCLauncher.Extract"))
            {
                if (!mutex.WaitOne(TimeSpan.FromMinutes(3)))
                    throw new TimeoutException("等待解压运行时超时");

                try
                {
                    if (File.Exists(marker))
                        return;
                    if (Directory.Exists(runtimeDir))
                        Directory.Delete(runtimeDir, true);
                    Directory.CreateDirectory(runtimeDir);
                    ExtractPayload(runtimeDir);
                    File.WriteAllText(marker, hash);
                }
                finally
                {
                    mutex.ReleaseMutex();
                }
            }
        }

        private static void ExtractPayload(string destination)
        {
            using (Stream stream = Assembly.GetExecutingAssembly()
                       .GetManifestResourceStream(PayloadName))
            {
                if (stream == null)
                    throw new InvalidOperationException("缺少内置 payload.zip");
                using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read))
                {
                    string root = Path.GetFullPath(destination)
                        .TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        string target = Path.GetFullPath(
                            Path.Combine(destination, entry.FullName));
                        if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                            throw new InvalidDataException(
                                "压缩包包含非法路径: " + entry.FullName);

                        if (string.IsNullOrEmpty(entry.Name))
                        {
                            Directory.CreateDirectory(target);
                        }
                        else
                        {
                            string parent = Path.GetDirectoryName(target);
                            if (!string.IsNullOrEmpty(parent))
                                Directory.CreateDirectory(parent);
                            entry.ExtractToFile(target, true);
                        }
                    }
                }
            }
        }

        private static string PrepareDataDir(string exeDir, string cacheRoot)
        {
            string candidate = Path.Combine(exeDir, "PyMCLauncher-data");
            try
            {
                Directory.CreateDirectory(candidate);
                string probe = Path.Combine(candidate, ".write-test");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);
                return candidate;
            }
            catch
            {
                string fallback = Path.Combine(cacheRoot, "data");
                Directory.CreateDirectory(fallback);
                return fallback;
            }
        }

        private static void StartLauncher(string runtimeDir, string dataDir)
        {
            string python = Path.Combine(runtimeDir, "pythonw.exe");
            if (!File.Exists(python))
                python = Path.Combine(runtimeDir, "python.exe");
            string script = Path.Combine(runtimeDir, "launcher.py");
            if (!File.Exists(script))
                throw new FileNotFoundException("找不到 launcher.py", script);

            ProcessStartInfo psi = NewPythonStartInfo(python, runtimeDir, dataDir);
            psi.Arguments = Quote(script);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            Process.Start(psi);
        }

        private static int RunSelfTest(string runtimeDir, string dataDir)
        {
            string python = Path.Combine(runtimeDir, "python.exe");
            string code = "import tkinter, httpx, core.config, ui.app; "
                        + "print('selftest ok', httpx.__version__)";
            ProcessStartInfo psi = NewPythonStartInfo(python, runtimeDir, dataDir);
            psi.Arguments = "-c " + Quote(code);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;

            using (Process process = Process.Start(psi))
            {
                string output = process.StandardOutput.ReadToEnd();
                string error = process.StandardError.ReadToEnd();
                process.WaitForExit();
                Log("selftest exit=" + process.ExitCode +
                    " out=" + output.Trim() + " err=" + error.Trim());
                return process.ExitCode;
            }
        }

        private static ProcessStartInfo NewPythonStartInfo(
            string python, string runtimeDir, string dataDir)
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python;
            psi.WorkingDirectory = runtimeDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["PYMCL_DATA_DIR"] = dataDir;
            psi.EnvironmentVariables["TCL_LIBRARY"] =
                Path.Combine(runtimeDir, "tcl", "tcl8.6");
            psi.EnvironmentVariables["TK_LIBRARY"] =
                Path.Combine(runtimeDir, "tcl", "tk8.6");
            return psi;
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static string BootLogPath()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "PyMCLauncher", "boot.log");
        }

        private static void Log(string message)
        {
            try
            {
                string path = BootLogPath();
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.AppendAllText(path,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " +
                    message + Environment.NewLine, Encoding.UTF8);
            }
            catch
            {
                // 日志失败不影响启动
            }
        }
    }
}
