import re
import time
import subprocess
import urllib.request
from typing import Dict, Any, Optional

from core.config import OS_NAME
from core.logger import cprint
from core.progress import ProgressBar


class SpeedTest:
    @staticmethod
    def test_download_speed(url: Optional[str] = None, duration: int = 10) -> Dict[str, Any]:
        cprint("Testing internet speed...", "INFO")
        
        start_time = time.time()
        downloaded_bytes = 0
        
        try:
            # Use a reliable speed test file
            if not url:
                test_urls = [
                    "http://speedtest.tele2.net/10MB.zip",
                    "https://proof.ovh.net/files/10Mb.dat",
                    "http://ipv4.download.thinkbroadband.com/10MB.zip"
                ]
                url = test_urls[0]  # Use first available
            
            cprint(f"Testing download speed from: {url}", "INFO")
            
            request = urllib.request.Request(url)
            with urllib.request.urlopen(request, timeout=30) as response:
                total_size = int(response.info().get("Content-Length", 10*1024*1024))  # Default 10MB
                
                tracker = ProgressBar(min(total_size, 50*1024*1024), "Speed Test", "B")  # Cap at 50MB
                
                while time.time() - start_time < duration:
                    try:
                        chunk = response.read(32768)  # 32KB chunks
                        if not chunk:
                            break
                        downloaded_bytes += len(chunk)
                        tracker.update(len(chunk))
                    except:
                        break
                
                tracker.finish()
                
            elapsed_time = time.time() - start_time
            if elapsed_time > 0:
                download_rate_bps = (downloaded_bytes * 8) / elapsed_time
                download_rate_mbps = download_rate_bps / 1000000
            else:
                download_rate_mbps = 0
            
            result = {
                "ok": True,
                "download_mbps": round(download_rate_mbps, 2),
                "downloaded_mb": round(downloaded_bytes / 1024 / 1024, 2),
                "elapsed_seconds": round(elapsed_time, 2),
            }
            cprint(f"Speed test complete: {result['download_mbps']} Mbps ({result['downloaded_mb']} MB downloaded)", "SUCCESS")
            return result
            
        except Exception as e:
            cprint(f"Speed test failed: {e}", "ERROR")
            return {"ok": False, "error": str(e)}

    @staticmethod
    def ping_test() -> Dict[str, Any]:
        cprint("Starting network latency test...", "INFO")
        
        hosts = ["google.com", "github.com", "cloudflare.com", "8.8.8.8"]
        results = {}
        
        progress = ProgressBar(len(hosts), "Ping test", "hosts")
        
        for host in hosts:
            try:
                if OS_NAME == "Windows":
                    command = ["ping", "-n", "1", "-w", "5000", host]
                else:
                    command = ["ping", "-c", "1", "-W", "5", host]
                    
                process = subprocess.run(command, capture_output=True, text=True, timeout=10)
                output = process.stdout
                
                # Parse ping output for latency
                if OS_NAME == "Windows":
                    latency_match = re.search(r"time[<=](\d+)ms", output)
                else:
                    latency_match = re.search(r"time=(\d+\.?\d*)\s*ms", output)
                
                if latency_match:
                    latency = float(latency_match.group(1))
                    results[host] = {"ok": True, "latency_ms": latency}
                    cprint(f"{host}: {latency}ms", "SUCCESS")
                else:
                    results[host] = {"ok": False, "msg": "Could not parse ping output"}
                    cprint(f"{host}: Could not parse ping output", "WARNING")
                    
            except subprocess.TimeoutExpired:
                results[host] = {"ok": False, "msg": "Timed out"}
                cprint(f"{host}: Timed out", "WARNING")
            except Exception as e:
                results[host] = {"ok": False, "msg": str(e)}
                cprint(f"{host}: {str(e)}", "ERROR")
            
            progress.update()
            
        progress.finish()
        
        # Calculate average latency
        successful_pings = [r["latency_ms"] for r in results.values() if r.get("ok")]
        if successful_pings:
            avg_latency = sum(successful_pings) / len(successful_pings)
            cprint(f"Average latency: {avg_latency:.1f}ms", "SUCCESS")
        
        return results