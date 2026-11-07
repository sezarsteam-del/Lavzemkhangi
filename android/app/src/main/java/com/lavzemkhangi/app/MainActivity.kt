package com.lavzemkhangi.app

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.BitmapFactory
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ImageView
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.NumberFormat
import java.util.Locale
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import java.security.KeyStore

private data class ShopProduct(
    val id: Int,
    val name: String,
    val category: String,
    val categoryId: Int,
    val description: String,
    val price: Long,
    val imageUrl: String
)

class MainActivity : Activity() {
    private val apiBase = "https://lavzemkhangi.ethiodeploy.app/api/android/v1"
    private val bg = Color.rgb(15, 15, 17)
    private val panel = Color.rgb(27, 27, 30)
    private val panel2 = Color.rgb(36, 35, 37)
    private val gold = Color.rgb(211, 174, 94)
    private val cream = Color.rgb(244, 239, 226)
    private val muted = Color.rgb(166, 161, 151)
    private val executor = Executors.newSingleThreadExecutor()
    private val products = mutableListOf<ShopProduct>()
    private val categories = mutableListOf<Pair<Int, String>>()
    private val cart = linkedMapOf<Int, Int>()
    private lateinit var content: LinearLayout
    private lateinit var search: EditText
    private var selectedCategory = 0
    private var currentTab = "خانه"
    private val prefs by lazy { getSharedPreferences("secure_session", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS)
        window.decorView.layoutDirection = View.LAYOUT_DIRECTION_RTL
        showHome()
        loadStoreData()
    }

    private fun loadStoreData() {
        api("/categories") { result, error ->
            if (result != null) {
                categories.clear()
                val arr = result.optJSONArray("categories") ?: JSONArray()
                for (i in 0 until arr.length()) {
                    val item = arr.optJSONObject(i) ?: continue
                    categories.add(Pair(item.optInt("id"), item.optString("name")))
                }
                showHome()
            }
        }
        loadProducts()
    }

    private fun loadProducts() {
        val q = if (::search.isInitialized) search.text.toString().trim() else ""
        val path = "/products?page=1" + if (selectedCategory > 0) "&category_id=" + selectedCategory else ""
        val queryPath = path + if (q.isNotBlank()) "&q=" + java.net.URLEncoder.encode(q, "UTF-8") else ""
        api(queryPath) { result, error ->
            if (result != null) {
                products.clear()
                val arr = result.optJSONArray("products") ?: JSONArray()
                for (i in 0 until arr.length()) {
                    val item = arr.optJSONObject(i) ?: continue
                    products.add(ShopProduct(
                        item.optInt("id"),
                        item.optString("name"),
                        item.optString("category_name", "بدون دسته‌بندی"),
                        item.optInt("category_id"),
                        item.optString("description"),
                        item.optLong("price"),
                        item.optString("image_url")
                    ))
                }
                if (::content.isInitialized) renderProducts()
            } else if (error != null) {
                toast(error)
            }
        }
    }

    private fun api(path: String, method: String = "GET", body: JSONObject? = null, callback: (JSONObject?, String?) -> Unit) {
        executor.execute {
            var connection: HttpURLConnection? = null
            try {
                connection = (URL(apiBase + path).openConnection() as HttpURLConnection).apply {
                    requestMethod = method
                    connectTimeout = 15000
                    readTimeout = 20000
                    setRequestProperty("Accept", "application/json")
                    val token = getToken()
                    if (!token.isNullOrBlank()) setRequestProperty("Authorization", "Bearer " + token)
                    if (body != null) {
                        doOutput = true
                        setRequestProperty("Content-Type", "application/json; charset=utf-8")
                        outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
                    }
                }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val raw = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: "{}"
                val json = JSONObject(raw)
                if (code in 200..299 && json.optBoolean("ok", false)) {
                    runOnUiThread { callback(json, null) }
                } else {
                    val message = json.optString("error", "ارتباط با فروشگاه انجام نشد.")
                    runOnUiThread { callback(null, message) }
                }
            } catch (e: Exception) {
                runOnUiThread { callback(null, "اتصال به فروشگاه برقرار نشد. اینترنت را بررسی کنید.") }
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val alias = "lavzemkhangi_access_token"
        val existing = store.getKey(alias, null)
        if (existing is SecretKey) return existing
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(
            alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true).build())
        return generator.generateKey()
    }

    private fun saveToken(token: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val combined = cipher.iv + encrypted
        prefs.edit().putString("token", Base64.encodeToString(combined, Base64.NO_WRAP)).apply()
    }

    private fun getToken(): String? {
        return try {
            val encoded = prefs.getString("token", null) ?: return null
            val bytes = Base64.decode(encoded, Base64.NO_WRAP)
            if (bytes.size < 13) return null
            val iv = bytes.copyOfRange(0, 12)
            val encrypted = bytes.copyOfRange(12, bytes.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(encrypted), Charsets.UTF_8)
        } catch (_: Exception) {
            prefs.edit().remove("token").apply()
            null
        }
    }

    private fun showHome() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(bg)
            layoutDirection = View.LAYOUT_DIRECTION_RTL
        }
        val scroll = ScrollView(this).apply { isFillViewport = true }
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(26))
        }
        scroll.addView(content)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(bottomBar(), LinearLayout.LayoutParams(-1, dp(68)))
        setContentView(root)
        addHeader()
        addHero()
        addSectionTitle("دسته‌بندی‌ها", "انتخاب گروه کالا")
        addCategories()
        addSectionTitle("محصولات فروشگاه", "قیمت و اطلاعات واقعی")
        search = EditText(this).apply {
            hint = "جست‌وجوی محصول..."
            setHintTextColor(muted)
            setTextColor(cream)
            textSize = 14f
            isSingleLine = true
            setPadding(dp(15), 0, dp(15), 0)
            background = rounded(panel2, 14)
            setOnEditorActionListener { _, _, _ -> loadProducts(); true }
        }
        content.addView(search, LinearLayout.LayoutParams(-1, dp(48)).apply { bottomMargin = dp(14) })
        val productContainer = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; tag = "products" }
        content.addView(productContainer)
        renderProducts()
        addFooterNote()
    }

    private fun addHeader() {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val brand = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        brand.addView(label("لوازم خانگی", 23f, gold, true))
        brand.addView(label("انتخابی ماندگار برای خانه شما", 11f, muted))
        row.addView(brand, LinearLayout.LayoutParams(0, -2, 1f))
        val cartButton = actionButton("سبد خرید  " + cart.values.sum(), true)
        cartButton.setOnClickListener { showCart() }
        row.addView(cartButton)
        content.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(22) })
    }

    private fun addHero() {
        val hero = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(20), dp(20), dp(20))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR,
                intArrayOf(Color.rgb(54, 46, 32), Color.rgb(27, 26, 25), Color.rgb(19, 19, 21))
            ).apply { cornerRadius = dp(20).toFloat(); setStroke(dp(1), Color.rgb(105, 84, 48)) }
        }
        hero.addView(label("تجربه‌ای لوکس برای خانه", 20f, cream, true))
        hero.addView(label("محصولات واقعی فروشگاه را ببینید و سفارش خود را ثبت کنید.", 13f, muted).apply {
            setPadding(0, dp(8), 0, dp(14))
        })
        val cta = actionButton("مشاهده محصولات", true)
        cta.setOnClickListener { search.requestFocus() }
        hero.addView(cta, LinearLayout.LayoutParams(-2, dp(42)))
        content.addView(hero, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(25) })
    }

    private fun addSectionTitle(title: String, subtitle: String) {
        val row = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        row.addView(label(title, 17f, cream, true), LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(label(subtitle, 11f, gold))
        content.addView(row, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(13) })
    }

    private fun addCategories() {
        val horizontal = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val list = mutableListOf(Pair(0, "همه"))
        list.addAll(categories)
        list.forEach { pair ->
            val chip = TextView(this).apply {
                text = pair.second
                textSize = 12f
                setTextColor(if (selectedCategory == pair.first) bg else cream)
                gravity = Gravity.CENTER
                setPadding(dp(14), dp(10), dp(14), dp(10))
                background = rounded(if (selectedCategory == pair.first) gold else panel2, 30)
                setOnClickListener { selectedCategory = pair.first; showHome(); loadProducts() }
            }
            horizontal.addView(chip, LinearLayout.LayoutParams(-2, dp(39)).apply { marginEnd = dp(8) })
        }
        val scroller = HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(horizontal)
        }
        content.addView(scroller, LinearLayout.LayoutParams(-1, dp(44)).apply { bottomMargin = dp(22) })
    }

    private fun renderProducts() {
        if (!::content.isInitialized) return
        val container = content.findViewWithTag<LinearLayout>("products") ?: return
        container.removeAllViews()
        if (products.isEmpty()) {
            container.addView(label("در حال دریافت محصولات فروشگاه...", 13f, muted))
            return
        }
        val term = if (::search.isInitialized) search.text.toString().trim() else ""
        val filtered = products.filter { term.isBlank() || it.name.contains(term, true) || it.category.contains(term, true) }
        if (filtered.isEmpty()) container.addView(label("محصولی پیدا نشد.", 13f, muted))
        filtered.forEach { product ->
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(13), dp(14), dp(13))
                background = rounded(panel, 17)
            }
            val art = ImageView(this).apply {
                scaleType = ImageView.ScaleType.CENTER_CROP
                background = rounded(panel2, 14)
                setImageDrawable(null)
            }
            card.addView(art, LinearLayout.LayoutParams(dp(64), dp(68)).apply { marginEnd = dp(13) })
            loadProductImage(art, product.imageUrl)
            val details = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            details.addView(label(product.name, 15f, cream, true))
            details.addView(label(product.category, 11f, muted).apply { setPadding(0, dp(4), 0, dp(6)) })
            details.addView(label(money(product.price) + " تومان", 12f, gold, true))
            card.addView(details, LinearLayout.LayoutParams(0, -2, 1f))
            val viewButton = TextView(this).apply {
                text = "افزودن"
                textSize = 11f
                setTextColor(bg)
                setPadding(dp(11), dp(10), dp(11), dp(10))
                background = rounded(gold, 10)
                setOnClickListener {
                    cart[product.id] = (cart[product.id] ?: 0) + 1
                    toast("به سبد خرید اضافه شد")
                    showHome()
                }
            }
            card.addView(viewButton)
            card.setOnClickListener { showProduct(product) }
            container.addView(card, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(11) })
        }
    }

    private fun loadProductImage(target: ImageView, imageUrl: String) {
        if (!imageUrl.startsWith("https://")) return
        executor.execute {
            try {
                val connection = URL(imageUrl).openConnection() as HttpURLConnection
                connection.connectTimeout = 10000
                connection.readTimeout = 15000
                connection.instanceFollowRedirects = true
                connection.setRequestProperty("Accept", "image/*")
                val bitmap = connection.inputStream.use { BitmapFactory.decodeStream(it) }
                connection.disconnect()
                if (bitmap != null) runOnUiThread { if (!isFinishing) target.setImageBitmap(bitmap) }
            } catch (_: Exception) { }
        }
    }

    private fun showProduct(product: ShopProduct) {
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(22), dp(18), dp(22), dp(8)); setBackgroundColor(panel) }
        body.addView(label("✦", 42f, gold))
        body.addView(label(product.name, 20f, cream, true).apply { setPadding(0, dp(10), 0, dp(8)) })
        body.addView(label(product.description, 14f, muted))
        body.addView(label(money(product.price) + " تومان", 17f, gold, true).apply { setPadding(0, dp(12), 0, dp(8)) })
        AlertDialog.Builder(this).setView(body).setNegativeButton("بستن", null)
            .setPositiveButton("افزودن به سبد") { _, _ ->
                cart[product.id] = (cart[product.id] ?: 0) + 1
                showHome()
            }.show().window?.setBackgroundDrawable(rounded(panel, 18))
    }

    private fun showCart() {
        if (cart.isEmpty()) {
            AlertDialog.Builder(this).setTitle("سبد خرید").setMessage("سبد خرید شما خالی است.")
                .setPositiveButton("باشه", null).show()
            return
        }
        val lines = cart.mapNotNull { entry ->
            products.find { it.id == entry.key }?.let { it.name + " × " + entry.value + "  |  " + money(it.price * entry.value) + " تومان" }
        }
        val total = cart.entries.sumOf { entry -> (products.find { it.id == entry.key }?.price ?: 0L) * entry.value }
        AlertDialog.Builder(this).setTitle("سبد خرید")
            .setMessage(lines.joinToString("\n") + "\n\nجمع کل: " + money(total) + " تومان")
            .setNeutralButton("خالی کردن") { _, _ -> cart.clear(); showHome() }
            .setNegativeButton("ادامه خرید", null)
            .setPositiveButton("ادامه و ثبت سفارش") { _, _ -> checkoutDialog() }.show()
    }

    private fun checkoutDialog() {
        if (getToken().isNullOrBlank()) {
            AlertDialog.Builder(this).setMessage("برای ثبت سفارش ابتدا وارد حساب مشتری شوید.")
                .setNegativeButton("بعداً", null).setPositiveButton("ورود / ثبت‌نام") { _, _ -> authDialog() }.show()
            return
        }
        val address = EditText(this).apply {
            hint = "آدرس کامل گیرنده (استان، شهر، خیابان، پلاک)"
            setTextColor(cream); setHintTextColor(muted); minLines = 3; gravity = Gravity.TOP
            setPadding(dp(14), dp(12), dp(14), dp(12)); background = rounded(panel2, 12)
        }
        val wrap = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(12), dp(18), 0); addView(label("آدرس تحویل سفارش", 14f, gold, true)); addView(address) }
        AlertDialog.Builder(this).setTitle("ثبت سفارش").setView(wrap)
            .setNegativeButton("بازگشت", null)
            .setPositiveButton("ثبت نهایی سفارش") { _, _ ->
                val items = JSONArray()
                cart.forEach { (id, qty) -> items.put(JSONObject().put("product_id", id).put("quantity", qty)) }
                val payload = JSONObject().put("shipping_address", address.text.toString().trim()).put("items", items)
                api("/orders", "POST", payload) { result, error ->
                    if (result != null) {
                        val order = result.optJSONObject("order")
                        cart.clear()
                        showHome()
                        AlertDialog.Builder(this).setTitle("سفارش ثبت شد")
                            .setMessage("شماره سفارش: " + (order?.optLong("id") ?: 0) + "\nسفارش شما در انتظار تأیید فروشگاه است. برای هماهنگی با شما تماس گرفته می‌شود.")
                            .setPositiveButton("متوجه شدم", null).show()
                    } else toast(error ?: "ثبت سفارش ناموفق بود.")
                }
            }.show()
    }

    private fun authDialog() {
        val fields = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(8), dp(18), 0) }
        val phone = field("شماره موبایل")
        val password = field("رمز عبور")
        password.inputType = 129
        val first = field("نام (فقط برای ثبت‌نام)")
        val last = field("نام خانوادگی (فقط برای ثبت‌نام)")
        fields.addView(phone); fields.addView(password); fields.addView(first); fields.addView(last)
        val register = TextView(this).apply { text = "ثبت‌نام: نام و نام خانوادگی را هم تکمیل کنید"; setTextColor(gold); textSize = 11f; setPadding(0, dp(8), 0, 0) }
        fields.addView(register)
        AlertDialog.Builder(this).setTitle("ورود یا ساخت حساب مشتری").setView(fields)
            .setNegativeButton("انصراف", null)
            .setNeutralButton("ثبت‌نام") { _, _ ->
                val body = JSONObject().put("phone", phone.text.toString()).put("password", password.text.toString())
                    .put("first_name", first.text.toString()).put("last_name", last.text.toString())
                api("/auth/register", "POST", body) { result, error ->
                    if (result != null) { saveToken(result.optString("token")); toast("حساب شما ساخته شد."); showAccount() }
                    else toast(error ?: "ثبت‌نام انجام نشد.")
                }
            }
            .setPositiveButton("ورود") { _, _ ->
                val body = JSONObject().put("phone", phone.text.toString()).put("password", password.text.toString())
                api("/auth/login", "POST", body) { result, error ->
                    if (result != null) { saveToken(result.optString("token")); toast("با موفقیت وارد شدید."); showAccount() }
                    else toast(error ?: "ورود انجام نشد.")
                }
            }.show()
    }

    private fun showAccount() {
        if (getToken().isNullOrBlank()) { authDialog(); return }
        api("/me") { result, error ->
            if (result == null) {
                prefs.edit().remove("token").apply()
                toast(error ?: "لطفاً دوباره وارد شوید.")
                authDialog()
                return@api
            }
            val customer = result.optJSONObject("customer")
            val name = customer?.optString("first_name", "") + " " + customer?.optString("last_name", "")
            AlertDialog.Builder(this).setTitle("حساب مشتری")
                .setMessage("خوش آمدید، " + name + "\n" + (customer?.optString("phone") ?: ""))
                .setItems(arrayOf("سفارش‌های من", "خروج از حساب")) { _, which ->
                    if (which == 0) showOrders() else {
                        api("/auth/logout", "POST", JSONObject()) { _, _ ->
                            prefs.edit().remove("token").apply()
                            toast("از حساب خارج شدید.")
                        }
                    }
                }.setNegativeButton("بستن", null).show()
        }
    }

    private fun showOrders() {
        api("/orders") { result, error ->
            if (result == null) { toast(error ?: "دریافت سفارش‌ها ناموفق بود."); return@api }
            val arr = result.optJSONArray("orders") ?: JSONArray()
            val lines = mutableListOf<String>()
            for (i in 0 until arr.length()) {
                val order = arr.optJSONObject(i) ?: continue
                lines.add("سفارش شماره " + order.optLong("id") + "  |  " + money(order.optLong("total_amount")) + " تومان\nوضعیت: " + order.optString("status") + " / " + order.optString("fulfillment_status"))
            }
            AlertDialog.Builder(this).setTitle("سفارش‌های من")
                .setMessage(if (lines.isEmpty()) "هنوز سفارشی ثبت نکرده‌اید." else lines.joinToString("\n\n"))
                .setPositiveButton("باشه", null).show()
        }
    }

    private fun bottomBar(): View {
        val bar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER; setBackgroundColor(Color.rgb(23, 23, 25)); setPadding(dp(8), dp(7), dp(8), dp(7)) }
        listOf("خانه" to "⌂", "دسته‌ها" to "▦", "سبد خرید" to "🛒", "حساب من" to "♙").forEach { pair ->
            val name = pair.first
            val item = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
                setOnClickListener {
                    when (name) {
                        "سبد خرید" -> showCart()
                        "حساب من" -> showAccount()
                        "دسته‌ها" -> { selectedCategory = 0; showHome() }
                        else -> { currentTab = name; showHome() }
                    }
                }
            }
            item.addView(label(pair.second, 20f, if (currentTab == name) gold else muted))
            item.addView(label(name, 10f, if (currentTab == name) gold else muted))
            bar.addView(item, LinearLayout.LayoutParams(0, -1, 1f))
        }
        return bar
    }

    private fun field(hintText: String): EditText = EditText(this).apply {
        hint = hintText; setTextColor(cream); setHintTextColor(muted); textSize = 14f; isSingleLine = true
        setPadding(dp(12), dp(8), dp(12), dp(8)); background = rounded(panel2, 10)
        layoutParams = LinearLayout.LayoutParams(-1, dp(48)).apply { bottomMargin = dp(9) }
    }

    private fun addFooterNote() {
        content.addView(label("فروشگاه مستقل اندروید • متصل به محصولات و سفارش‌های فروشگاه", 10f, muted)
            .apply { gravity = Gravity.CENTER; setPadding(0, dp(12), 0, dp(4)) })
    }

    private fun actionButton(text: String, primary: Boolean): Button = Button(this).apply {
        this.text = text; textSize = 12f; isAllCaps = false
        setTextColor(if (primary) bg else gold); background = rounded(if (primary) gold else panel2, 12)
        minHeight = 0; minimumHeight = 0; setPadding(dp(14), 0, dp(14), 0)
    }

    private fun label(text: String, size: Float, color: Int, bold: Boolean = false) =
        TextView(this).apply {
            this.text = text; textSize = size; setTextColor(color)
            typeface = Typeface.create("sans-serif", if (bold) Typeface.BOLD else Typeface.NORMAL)
            gravity = Gravity.CENTER_VERTICAL
        }

    private fun rounded(color: Int, radius: Int) = GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat() }
    private fun money(value: Long): String = NumberFormat.getNumberInstance(Locale("fa", "IR")).format(value)
    private fun toast(message: String) { if (!isFinishing) Toast.makeText(this, message, Toast.LENGTH_LONG).show() }
    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    override fun onDestroy() {
        executor.shutdown()
        super.onDestroy()
    }
}
