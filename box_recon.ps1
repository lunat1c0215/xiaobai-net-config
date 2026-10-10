# box_recon.ps1 - 小白盒子 / 网络打印盒子 侦察脚本
# 用法:
#   .\box_recon.ps1                 # 自动取当前网关
#   .\box_recon.ps1 -Target 192.168.4.1
#   .\box_recon.ps1 -Target 192.168.4.1 -FullScan   # 全端口 1-65535
#
# 用途: 让盒子进入配网(AP)模式后，电脑连它的热点，用本脚本摸清它暴露的配置接口

param(
  [string]$Target = "",
  [switch]$FullScan,
  [int]$TimeoutMs = 400
)

$ErrorActionPreference = 'SilentlyContinue'

function Get-DefaultGateway {
  $r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
       Sort-Object RouteMetric | Select-Object -First 1
  if ($r) { return $r.NextHop }
  return $null
}

if (-not $Target) {
  $Target = Get-DefaultGateway
  Write-Host "[*] 未指定目标，使用当前网关: $Target"
}
if (-not $Target) { Write-Host "[!] 拿不到网关，请手动 -Target <IP>"; exit 1 }

Add-Type -TypeDefinition @'
using System; using System.Net; using System.Net.Sockets; using System.Collections.Generic; using System.Threading; using System.Text;
public class Recon {
  // ---- TCP 端口扫描 ----
  public static string TcpScan(string ip, int[] ports, int tmo, int conc){
    var open=new List<int>(); var ts=new List<Thread>();
    var q=new System.Collections.Concurrent.ConcurrentQueue<int>();
    foreach(int p in ports) q.Enqueue(p);
    int n=Math.Min(conc, ports.Length);
    for(int i=0;i<n;i++){
      var t=new Thread(()=>{ int p;
        while(q.TryDequeue(out p)){ try{ using(var c=new TcpClient()){
          var ta=c.ConnectAsync(ip,p); if(ta.Wait(tmo)&&c.Connected){ lock(open){ open.Add(p); } } } }catch{} } });
      t.IsBackground=true; t.Start(); ts.Add(t);
    }
    foreach(var t in ts) t.Join();
    open.Sort(); return string.Join(",",open);
  }
  public static string TcpFull(string ip,int tmo,int conc){
    var ports=new int[65535]; for(int i=0;i<65535;i++) ports[i]=i+1;
    return TcpScan(ip,ports,tmo,conc);
  }
  // ---- UDP 探测 ----
  public static string UdpProbe(string ip, int[] ports, int tmo){
    var sb=new StringBuilder();
    foreach(int p in ports){
      try{ using(var u=new UdpClient()){ u.Client.ReceiveTimeout=tmo; u.Connect(ip,p);
        var b=new byte[]{0,0,0,0}; u.Send(b,b.Length);
        var ep=new IPEndPoint(IPAddress.Any,0);
        try{ var r=u.Receive(ref ep); sb.AppendLine("    UDP "+p+" : REPLY "+r.Length+"B"); }
        catch(SocketException se){ if(se.SocketErrorCode==SocketError.ConnectionReset) sb.AppendLine("    UDP "+p+" : closed"); }
      } }catch{}
    }
    return sb.ToString();
  }
  // ---- HTTP 抓取 ----
  public static string HttpGet(string url,int tmo){
    try{
      var req=(HttpWebRequest)WebRequest.Create(url); req.Timeout=tmo; req.AllowAutoRedirect=false;
      req.UserAgent="Mozilla/5.0";
      using(var resp=(HttpWebResponse)req.GetResponse()){
        using(var sr=new System.IO.StreamReader(resp.GetResponseStream())){
          var body=sr.ReadToEnd(); if(body.Length>800) body=body.Substring(0,800);
          return (int)resp.StatusCode+" | server="+resp.Headers["Server"]+" | "+body.Replace("\r"," ").Replace("\n"," ");
        }
      }
    }catch(Exception e){ return "ERR "+e.Message; }
  }
}
'@

$common = @(21,22,23,53,80,81,443,554,631,1900,2080,3000,5000,5353,7681,8000,8080,8081,8443,8888,9000,9100,9999,10000,10001,20000,33333,45678,49152,50000)
$udps   = @(53,67,68,69,123,137,138,161,1900,3478,3702,4500,5353,5355,5000,8000,8080,8888,9000,9999,10000,10001,33333,45678,50000)

Write-Host "`n=== 目标: $Target ==="

Write-Host "`n[1/5] Ping"
$ping = Test-Connection -ComputerName $Target -Count 2 -Quiet
Write-Host "    alive = $ping"

Write-Host "`n[2/5] MAC / ARP"
arp -a | Select-String -Pattern ([regex]::Escape($Target)) | ForEach-Object { "    " + $_.Line.Trim() }
$mac = (Get-NetNeighbor -IPAddress $Target -ErrorAction SilentlyContinue | Select-Object -First 1).LinkLayerAddress
if ($mac) {
  $oui = ($mac -replace '-',':').Substring(0,8)
  Write-Host "    MAC = $mac   OUI = $oui"
  try { $v = Invoke-RestMethod -TimeoutSec 10 -Uri "https://api.macvendors.com/$oui"; Write-Host "    OUI 厂商 = $v" } catch { Write-Host "    OUI 查询失败" }
}

Write-Host "`n[3/5] TCP 扫描"
if ($FullScan) {
  Write-Host "    (全端口 1-65535，约 30-60s)"
  $open = [Recon]::TcpFull($Target, $TimeoutMs, 600)
} else {
  $open = [Recon]::TcpScan($Target, $common, $TimeoutMs, 120)
}
Write-Host "    开放 TCP 端口: $open"

Write-Host "`n[4/5] UDP 探测"
$u = [Recon]::UdpProbe($Target, $udps, 350)
if ($u) { Write-Host $u } else { Write-Host "    (无 UDP 响应)" }

Write-Host "`n[5/5] HTTP 抓取"
foreach ($p in @(80,81,8080,8081,8000,8888,9000,3000,2080,443)) {
  $scheme = if ($p -eq 443) { 'https' } else { 'http' }
  $r = [Recon]::HttpGet("$scheme`://$Target`:$p/", 5000)
  if ($r -notlike 'ERR *') { Write-Host "    $scheme`://$Target`:$p/ -> $r" }
}

Write-Host "`n=== 完成 ==="
Write-Host "把上面输出整段发回即可继续。"
