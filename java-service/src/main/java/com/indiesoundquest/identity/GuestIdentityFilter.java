package com.indiesoundquest.identity;
import com.indiesoundquest.tournament.domain.GuestSession;
import com.indiesoundquest.tournament.repository.GuestSessionRepository;
import jakarta.servlet.*; import jakarta.servlet.http.*; import java.io.IOException; import java.nio.charset.StandardCharsets; import java.security.*; import java.util.*; import org.springframework.http.ResponseCookie; import org.springframework.stereotype.Component; import org.springframework.web.filter.OncePerRequestFilter;
@Component public class GuestIdentityFilter extends OncePerRequestFilter {
 public static final String ATTRIBUTE="guestSession"; private final GuestSessionRepository sessions;
 public GuestIdentityFilter(GuestSessionRepository sessions){this.sessions=sessions;}
 @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain)throws ServletException,IOException {String token=Arrays.stream(Optional.ofNullable(request.getCookies()).orElse(new Cookie[0])).filter(c->"isq_guest".equals(c.getName())).map(Cookie::getValue).findFirst().orElse(null); GuestSession guest;if(token==null){token=UUID.randomUUID()+"-"+UUID.randomUUID();guest=sessions.save(new GuestSession(UUID.randomUUID(),hash(token)));response.addHeader("Set-Cookie",ResponseCookie.from("isq_guest",token).httpOnly(true).sameSite("Lax").path("/").build().toString());}else { final String existingToken=token; guest=sessions.findByTokenHash(hash(existingToken)).orElseGet(()->sessions.save(new GuestSession(UUID.randomUUID(),hash(existingToken)))); } request.setAttribute(ATTRIBUTE,guest);chain.doFilter(request,response);}
 private String hash(String v){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(v.getBytes(StandardCharsets.UTF_8)));}catch(NoSuchAlgorithmException e){throw new IllegalStateException(e);}}
}
